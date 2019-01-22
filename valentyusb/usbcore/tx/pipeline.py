#!/usr/bin/env python3

from migen import *
from migen.genlib import cdc

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

        reset = Signal()
        stall = Signal()

        # 12MHz domain
        shifter = TxShifter(width=8)
        self.submodules.shifter = shifter = ClockDomainsRenamer("usb_12")(shifter)
        self.comb += [
            shifter.i_data.eq(self.i_data_payload),
            self.o_data_strobe.eq(shifter.o_get & ~stall & self.i_oe),

            shifter.reset.eq(reset),
            shifter.ce.eq(~stall),
        ]

        # FIXME: This is a horrible hack
        stalled_reset = Signal()
        reset_n1 = Signal() # Need to reset the bit stuffer 1 cycle after the shifter.
        i_oe_n1 = Signal()  # 1 cycle delay inside bit stuffer
        i_oe_n2 = Signal()  # Where does this delay come from?
        self.sync.usb_12 += [
            If(shifter.o_empty,
                stalled_reset.eq(~self.i_oe),
            ),
            If(~stall,
                reset.eq(stalled_reset),
            ),
            If(~stall,
                If(shifter.o_get,
                    i_oe_n1.eq(self.i_oe),
                ),
                reset_n1.eq(reset),
                i_oe_n2.eq(i_oe_n1),
            ),
        ]

        self.comb += [
        ]

        bitstuff = TxBitstuffer()
        self.submodules.bitstuff = ClockDomainsRenamer("usb_12")(bitstuff)
        self.comb += [
            bitstuff.i_data.eq(shifter.o_data),
            bitstuff.reset.eq(reset_n1),
            stall.eq(bitstuff.o_stall),
        ]

        # Cross the data from the 12MHz domain to the 48MHz domain
        fit_dat = Signal()
        fit_oe  = Signal()
        cdc_dat = cdc.MultiReg(bitstuff.o_data, fit_dat, odomain="usb_48", n=3)
        cdc_oe  = cdc.MultiReg(i_oe_n2, fit_oe, odomain="usb_48", n=3)
        self.specials += [cdc_dat, cdc_oe]

        # 48MHz domain
        # NRZI decoding
        nrzi = TxNRZIEncoder()
        self.submodules.nrzi = nrzi = ClockDomainsRenamer("usb_48")(nrzi)
        self.comb += [
            nrzi.i_valid.eq(self.i_bit_strobe),
            nrzi.i_data.eq(fit_dat),
            nrzi.i_oe.eq(fit_oe),

            self.o_usbp.eq(nrzi.o_usbp),
            self.o_usbn.eq(nrzi.o_usbn),
            self.o_oe.eq(nrzi.o_oe),
        ]
