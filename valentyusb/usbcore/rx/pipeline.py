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

        # NRZI decoding
        nrzi = RxNRZIDecoder()
        self.submodules.nrzi = nrzi = ClockDomainsRenamer("usb_48")(nrzi)
        self.comb += [
            nrzi.i_valid.eq(self.o_bit_strobe),
            nrzi.i_dj.eq(clock_data_recovery.line_state_dj),
            nrzi.i_dk.eq(clock_data_recovery.line_state_dk),
            nrzi.i_se0.eq(clock_data_recovery.line_state_se0),
        ]

        # Cross the data from the 48MHz domain to the 12MHz domain
        bit_dat = Signal()
        bit_se0 = Signal()
        self.comb += [
            bit_dat.eq(nrzi.o_data),
            bit_se0.eq(nrzi.o_se0),
        ]

        # The packet detector resets the reset of the pipeline.
        reset = Signal()
        detect = RxPacketDetect()
        self.submodules.detect = detect = ClockDomainsRenamer("usb_12")(detect)
        self.comb += [
            self.o_pkt_start.eq(detect.o_pkt_start),
            detect.i_data.eq(bit_dat),
            reset.eq(~detect.o_pkt_active),
            detect.reset.eq(bit_se0 | self.reset),
        ]

        last_stall = Signal()
        last_bit_dat = Signal()
        last_put = Signal()
        last_data = Signal(8)
        last_reset = Signal()
        last_se0 = Signal()

        bitstuff = RxBitstuffRemover()
        self.submodules.bitstuff = ClockDomainsRenamer("usb_12")(bitstuff)
        self.comb += [
            bitstuff.reset.eq(last_reset),
            bitstuff.i_data.eq(bit_dat),
        ]

        # 1bit->8bit (1byte) serial to parallel conversion
        shifter = RxShifter(width=8)
        self.submodules.shifter = shifter = ClockDomainsRenamer("usb_12")(shifter)
        self.comb += [
            shifter.reset.eq(last_reset),
            shifter.i_data.eq(last_bit_dat | bitstuff.o_stall),
            shifter.ce.eq(~bitstuff.o_stall),
        ]

        self.comb += [
            self.o_data_strobe.eq(last_put & ~last_stall),
            self.o_data_payload.eq(last_data),
        ]

        # Packet ended signal
        self.sync.usb_12 += [
            If(self.o_pkt_start,
                self.o_pkt_in_progress.eq(1),
            ).Elif(last_se0 & self.o_pkt_in_progress,
                self.o_pkt_end.eq(1),
                self.o_pkt_in_progress.eq(0),
            ).Else(
                self.o_pkt_end.eq(0),
            ),
            last_stall.eq(bitstuff.o_stall),
            last_bit_dat.eq(bit_dat),
            last_put.eq(shifter.o_put),
            last_data.eq(shifter.o_data[::-1]),
            last_reset.eq(reset),
            last_se0.eq(bit_se0),
        ]
