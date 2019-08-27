#!/usr/bin/env python3

import unittest

from migen import *
from migen.genlib import cdc

from litex.soc.cores.gpio import GPIOOut

from ..pid import PIDTypes
from ..tx.pipeline import TxPipeline
from ..tx.crc import TxParallelCrcGenerator
from ..utils.asserts import assertMultiLineEqualSideBySide
from ..utils.packet import *
from ..utils.pprint import pp_packet
from ..test.common import BaseUsbTestCase


class TxPacketSend(Module):
    def __init__(self, tx, auto_crc=True):
        self.submodules.tx = tx

        self.i_pkt_start = Signal()
        self.o_pkt_end = Signal()

        self.i_pid = Signal(4)
        self.i_data_payload = Signal(8)
        self.i_data_ready = Signal()
        self.o_data_ack = Signal()

        o_oe12 = Signal()
        self.specials += cdc.MultiReg(tx.o_oe, o_oe12, odomain="usb_12", n=1)

        pid = Signal(4)

        fsm = FSM()
        self.submodules.fsm = fsm = ClockDomainsRenamer("usb_12")(fsm)
        fsm.act('IDLE',
            NextValue(tx.i_oe, self.i_pkt_start),
            If(self.i_pkt_start,
                # If i_pkt_start is set, then send the next packet.
                # We pre-queue the SYNC byte here to cut down on latency.
                NextState('QUEUE_SYNC'),
            ).Else(
                NextValue(tx.i_oe, 0),
            )
        )

        # Send the QUEUE_SYNC byte
        fsm.act('QUEUE_SYNC',
            # The PID might change mid-sync, because we're still figuring
            # out what the response ought to be.
            NextValue(pid, self.i_pid),
            tx.i_data_payload.eq(1),
            If(tx.o_data_strobe,
                NextState('QUEUE_PID'),
            ),
        )

        # Send the PID byte
        fsm.act('QUEUE_PID',
            tx.i_data_payload.eq(Cat(pid, pid ^ 0b1111)),
            If(tx.o_data_strobe,
                If(pid & PIDTypes.TYPE_MASK == PIDTypes.HANDSHAKE,
                    NextState('WAIT_TRANSMIT'),
                ).Elif(pid & PIDTypes.TYPE_MASK == PIDTypes.DATA,
                    NextState('QUEUE_DATA0'),
                ).Else(
                    NextState('ERROR'),
                ),
            ),
        )

        nextstate = 'WAIT_TRANSMIT'
        if auto_crc:
            nextstate = 'QUEUE_CRC0'

        fsm.act('QUEUE_DATA0',
            If(~self.i_data_ready,
                NextState(nextstate),
            ).Else(
                NextState('QUEUE_DATAn'),
            ),
        )

        # Keep transmitting data bytes until the i_data_ready signal is not
        # high on a o_data_strobe event.
        fsm.act('QUEUE_DATAn',
            tx.i_data_payload.eq(self.i_data_payload),
            self.o_data_ack.eq(tx.o_data_strobe),
            If(~self.i_data_ready,
                NextState(nextstate),
            ),
        )

        if auto_crc:
            crc = TxParallelCrcGenerator(
                crc_width  = 16,
                data_width = 8,
                polynomial = 0b1000000000000101, # polynomial = (16, 15, 2, 0)
                initial    = 0b1111111111111111, # seed = 0xFFFF
            )
            self.submodules.crc = crc = ClockDomainsRenamer("usb_12")(crc)

            self.comb += [
                crc.i_data_payload.eq(self.i_data_payload),
                crc.reset.eq(fsm.ongoing('QUEUE_PID')),
                If(fsm.ongoing('QUEUE_DATAn'),
                    crc.i_data_strobe.eq(tx.o_data_strobe),
                ),
            ]

            fsm.act('QUEUE_CRC0',
                tx.i_data_payload.eq(crc.o_crc[:8]),
                If(tx.o_data_strobe,
                    NextState('QUEUE_CRC1'),
                ),
            )
            fsm.act('QUEUE_CRC1',
                tx.i_data_payload.eq(crc.o_crc[8:]),
                If(tx.o_data_strobe,
                    NextState('WAIT_TRANSMIT'),
                ),
            )

        fsm.act('WAIT_TRANSMIT',
            NextValue(tx.i_oe, 0),
            If(~o_oe12,
                self.o_pkt_end.eq(1),
                NextState('IDLE'),
            ),
        )

        fsm.act('ERROR')
