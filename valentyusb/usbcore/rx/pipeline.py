#!/usr/bin/env python3

from migen import *
from migen.genlib import cdc

import unittest

from .detect import RxPacketDetect
from .clock import RxClockDataRecovery
from .nrzi import RxNRZIDecoder


class RxPipeline(Module):
    def __init__(self, usbp_raw, usbn_raw):
        # 48MHz domain
        # Clock recovery
        self.o_bit_strobe = Signal()
        self.submodules.clock_data_recovery = clock_data_recovery = ClockDomainsRenamer("usb_48")(
            RxClockDataRecovery(usbp_raw, usbn_raw),
        )
        self.comb += [
            self.o_bit_strobe.eq(clock_data_recovery.line_state_valid),
        ]

        # NRZI decoding
        self.submodules.nrzi = nrzi = ClockDomainsRenamer("usb_48")(RxNRZIDecoder())
        self.comb += [
            nrzi.i_valid.eq(clock_data_recovery.line_state_valid),
            nrzi.i_dj.eq(clock_data_recovery.line_state_dj),
            nrzi.i_dk.eq(clock_data_recovery.line_state_dk),
            nrzi.i_se0(clock_data_recovery.line_state_se0),
        ]

        # Cross the data from the 48MHz domain to the 12MHz domain
        bit_dat = Signal()
        bit_se0 = Signal()
        self.specials += ClockDomainsRenamer("usb_12")(cdc.MultiReg(nrzi.o_data, bit_dat, n=3))
        self.specials += ClockDomainsRenamer("usb_12")(cdc.MultiReg(nrzi.o_se0,  bit_se0, n=3))

        reset = Signal()
        self.submodules.detect = detect = ClockDomainsRenamer("usb_12")(RxPacketDetect())
        self.comb += [
            detect.reset.eq(bit_se0),
            reset.eq(~detect.o_pkt_active),
        ]

        self.submodules.bitstuff = bitstuff = ClockDomainsRenamer("usb_12")(RxBitstuffRemover())
        self.comb += [
            bitstuff.reset.eq(reset),
            bitstuff.i_data.eq(bit_data),
        ]

        self.submodules.shifter = shifter = ClockDomainsRenamer("usb_12")(CEInserter(RxShifter(width=8)))
        self.comb += [
            shifter.reset.eq(reset),
            shifter.i_data.eq(bitstuff.o_data),
            shifter.ce.eq(~bitstuff.o_stall),
        ]

        self.o_data_strobe = Signal()
        self.o_data_payload = Signal(8)
        self.comb += [
            self.o_data_strobe.eq(shifter.o_put),
            self.o_data_payload.eq(shifter.o_data),
        ]



if __name__ == "__main__":
    unittest.main()
