#!/usr/bin/env python3

from migen import *
from migen.genlib import cdc

import unittest

from .bitstuff import RxBitstuffRemover
from .clock import RxClockDataRecovery
from .detect import RxPacketDetect
from .nrzi import RxNRZIDecoder
from .shifter import RxShifter
from ..utils.packet import b, nrzi
from ..test.common import BaseUsbTestCase


class RxPipeline(Module):
    def __init__(self):
        self.reset = Signal()

        # 12MHz USB alignment pulse in 48MHz clock domain
        self.o_bit_strobe = Signal()

        # Reset state is J
        self.i_usbp = Signal(reset=1)
        self.i_usbn = Signal(reset=0)

        self.o_data_strobe = Signal()
        self.o_data_payload = Signal(8)

        self.o_pkt_start = Signal()
        self.o_pkt_in_progress = Signal()
        self.o_pkt_end = Signal()

        # 48MHz domain
        # Clock recovery
        clock_data_recovery = RxClockDataRecovery(self.i_usbp, self.i_usbn)
        self.submodules.clock_data_recovery = ClockDomainsRenamer("usb_48")(clock_data_recovery)
        self.comb += [
            self.o_bit_strobe.eq(clock_data_recovery.line_state_valid),
        ]

        # A reset condition is one where the device is in SE0 for more
        # than 2.5 uS, which is ~30 bit times.
        self.o_reset = Signal()
        reset_counter = Signal(7)
        self.comb += self.o_reset.eq(reset_counter[6])
        self.sync.usb_48 += [
            If(clock_data_recovery.line_state_valid,
                If(clock_data_recovery.line_state_se0,
                    If(~reset_counter[6],
                        reset_counter.eq(reset_counter + 1),
                    )
                ).Else(
                    reset_counter.eq(0),
                )
            )
        ]

        # NRZI decoding
        nrzi = RxNRZIDecoder()
        self.submodules.nrzi = nrzi = ClockDomainsRenamer("usb_48")(nrzi)
        self.comb += [
            nrzi.i_valid.eq(self.o_bit_strobe),
            nrzi.i_dj.eq(clock_data_recovery.line_state_dj),
            nrzi.i_dk.eq(clock_data_recovery.line_state_dk),
            nrzi.i_se0.eq(clock_data_recovery.line_state_se0),
        ]

        # The packet detector asserts the reset of the pipeline.
        reset = Signal()
        detect = RxPacketDetect()
        self.submodules.detect = detect = ClockDomainsRenamer("usb_48")(detect)
        self.comb += [
            detect.reset.eq(self.reset),
            detect.i_valid.eq(nrzi.o_valid),
            detect.i_se0.eq(nrzi.o_se0),
            detect.i_data.eq(nrzi.o_data),
            reset.eq(~detect.o_pkt_active),
        ]

        bitstuff = RxBitstuffRemover()
        self.submodules.bitstuff = ClockDomainsRenamer("usb_48")(bitstuff)
        self.comb += [
            bitstuff.reset.eq(~detect.o_pkt_active | self.reset),
            bitstuff.i_valid.eq(nrzi.o_valid),
            bitstuff.i_data.eq(nrzi.o_data),
        ]

        last_reset = Signal()
        self.sync.usb_48 += [
            last_reset.eq(reset),
        ]

        # 1bit->8bit (1byte) serial to parallel conversion
        shifter = RxShifter(width=8)
        self.submodules.shifter = shifter = ClockDomainsRenamer("usb_48")(shifter)
        self.comb += [
            shifter.reset.eq(last_reset),
            shifter.i_data.eq(bitstuff.o_data),
            shifter.i_valid.eq(~bitstuff.o_stall & detect.o_pkt_active),
        ]

        # Cross the data from the 48MHz domain to the 12MHz domain
        flag_start = Signal()
        flag_end = Signal()
        flag_valid = Signal()
        payloadFifo = genlib.fifo.AsyncFIFO(8, 2)
        self.submodules.payloadFifo = payloadFifo = ClockDomainsRenamer({"write":"usb_48", "read":"usb_12"})(payloadFifo)

        self.comb += [
            payloadFifo.din.eq(shifter.o_data[::-1]),
            payloadFifo.we.eq(shifter.o_put),
            self.o_data_payload.eq(payloadFifo.dout),
            self.o_data_strobe.eq(payloadFifo.readable),
            payloadFifo.re.eq(1),
        ]

        flagsFifo = genlib.fifo.AsyncFIFO(2, 2)
        self.submodules.flagsFifo = flagsFifo = ClockDomainsRenamer({"write":"usb_48", "read":"usb_12"})(flagsFifo)

        self.comb += [
            flagsFifo.din[1].eq(detect.o_pkt_start),
            flagsFifo.din[0].eq(detect.o_pkt_end),
            flagsFifo.we.eq(detect.o_pkt_start | detect.o_pkt_end),
            flag_start.eq(flagsFifo.dout[1]),
            flag_end.eq(flagsFifo.dout[0]),
            flag_valid.eq(flagsFifo.readable),
            flagsFifo.re.eq(1),
        ]

        # Packet flag signals (in 12MHz domain)
        self.comb += [
            self.o_pkt_start.eq(flag_start & flag_valid),
            self.o_pkt_end.eq(flag_end & flag_valid),
        ]

        self.sync.usb_12 += [
            If(self.o_pkt_start,
                self.o_pkt_in_progress.eq(1),
            ).Elif(self.o_pkt_end,
                self.o_pkt_in_progress.eq(0),
            ),
        ]

