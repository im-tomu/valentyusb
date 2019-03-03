#!/usr/bin/env python3

from migen import *
from migen.genlib import cdc
from migen.genlib.fsm import FSM, NextState, NextValue

import unittest

from .bitstuff import TxBitstuffer
from .nrzi import TxNRZIEncoder
from .shifter import TxShifter
from ..utils.packet import b, nrzi, diff
from ..test.common import BaseUsbTestCase


class TxPipeline(Module):
    def __init__(self):
        self.i_bit_strobe = Signal()

        self.i_data_payload = Signal(8)
        self.o_data_strobe = Signal()

        self.i_oe = Signal()

        self.o_usbp = Signal()
        self.o_usbn = Signal()
        self.o_oe = Signal()

        self.o_pkt_end = Signal()

        fsm = FSM()
        self.submodules.tx_pipeline_fsm = ClockDomainsRenamer("usb_12")(fsm)
        shifter = TxShifter(width=8)
        self.submodules.shifter = ClockDomainsRenamer("usb_12")(shifter)
        bitstuff = TxBitstuffer()
        self.submodules.bitstuff = ClockDomainsRenamer("usb_12")(bitstuff)
        nrzi = TxNRZIEncoder()
        self.submodules.nrzi = ClockDomainsRenamer("usb_48")(nrzi)

        sync_pulse = Signal(8)
        sending_sync = Signal()

        fit_dat = Signal()
        fit_oe  = Signal()

        reset_shifter = Signal()
        reset_bitstuff = Signal() # Need to reset the bit stuffer 1 cycle after the shifter.
        stall = Signal()
        transmission_enabled = Signal()

        # 12MHz domain

        stalled_reset = Signal()
        bitstuff_valid_data = Signal()

        self.comb += [
            shifter.i_data.eq(self.i_data_payload),
            # Send a data strobe when we're two bits from the end of the sync pulse.
            # This is because the pipeline takes two bit times, and we want to ensure the pipeline
            # has spooled up enough by the time we're there.

            shifter.reset.eq(reset_shifter),
            shifter.ce.eq(~stall),
        ]

        fsm.act('IDLE',
            If(self.i_oe,
                NextState('SEND_SYNC'),
                NextValue(sync_pulse, 0b10000000),
                NextValue(sending_sync, 1),
                NextValue(transmission_enabled, 1),
            )
        )

        fsm.act('SEND_SYNC',
            NextValue(sync_pulse, sync_pulse >> 1),
            NextValue(transmission_enabled, 1),
            fit_dat.eq(sync_pulse[0]),
            fit_oe.eq(1),
            If(sync_pulse[0],
                NextValue(sending_sync, 0),
                NextState('SEND_DATA'),
                reset_bitstuff.eq(1),
            ).Elif(sync_pulse[1],
                reset_shifter.eq(1),
            ).Elif(sync_pulse[2],
                stalled_reset.eq(1),
                self.o_data_strobe.eq(1),
            ),
        )
        fsm.act('SEND_DATA',
            self.o_data_strobe.eq(shifter.o_get & ~stall & self.i_oe),
            fit_dat.eq(shifter.o_data & ~bitstuff.o_stall),
            fit_oe.eq(1),
            NextValue(transmission_enabled, 1),
            If(shifter.o_empty,
                stalled_reset.eq(~self.i_oe),
            ),
            If(~stall,
                reset_shifter.eq(stalled_reset),
                If(shifter.o_get,
                    bitstuff_valid_data.eq(self.i_oe),
                ),
                reset_bitstuff.eq(reset_shifter),
            ),
            If(~self.i_oe & shifter.o_empty & ~bitstuff.o_stall,
                NextValue(transmission_enabled, 0),
                NextState('IDLE')
            ),
        )

        self.comb += [
            bitstuff.i_data.eq(shifter.o_data),
            bitstuff.reset.eq(reset_bitstuff),
            stall.eq(bitstuff.o_stall),
        ]

        # 48MHz domain
        # NRZI decoding
        self.comb += [
            nrzi.i_valid.eq(self.i_bit_strobe),
            nrzi.i_data.eq(fit_dat),
            nrzi.i_oe.eq(fit_oe),

            self.o_usbp.eq(nrzi.o_usbp),
            self.o_usbn.eq(nrzi.o_usbn),
            self.o_oe.eq(nrzi.o_oe),
        ]