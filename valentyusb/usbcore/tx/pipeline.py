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

        tx_pipeline_fsm = FSM()
        self.submodules.tx_pipeline_fsm = ClockDomainsRenamer("usb_12")(tx_pipeline_fsm)
        shifter = TxShifter(width=8)
        self.submodules.shifter = ClockDomainsRenamer("usb_12")(shifter)
        bitstuff = TxBitstuffer()
        self.submodules.bitstuff = ClockDomainsRenamer("usb_12")(bitstuff)
        nrzi = TxNRZIEncoder()
        self.submodules.nrzi = ClockDomainsRenamer("usb_48")(nrzi)

        sync_pulse = Signal(8)

        self.fit_dat = fit_dat = Signal()
        self.fit_oe  = fit_oe  = Signal()

        reset_shifter = Signal()
        reset_bitstuff = Signal() # Need to reset the bit stuffer 1 cycle after the shifter.
        stall = Signal()
        tx_pipeline_fsm

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

        self.sync.usb_12 += sync_pulse.eq(sync_pulse >> 1)

        tx_pipeline_fsm.act('IDLE',
            If(self.i_oe,
                NextState('SEND_SYNC'),
                NextValue(sync_pulse, 1 << 7),
            )
        )

        tx_pipeline_fsm.act('SEND_SYNC',
            fit_oe.eq(1),

            fit_dat.eq(sync_pulse[0]),

            reset_bitstuff.eq(sync_pulse[0]),

            # The shifter has only one clock cycle of latency, so reset it
            # one cycle before the end of the sync byte.
            reset_shifter.eq(sync_pulse[1]),

            # The pipeline takes two bits to fill.  Reset the bitstuff stall
            # flag, and request the next byte from the module controlling us.
            stalled_reset.eq(sync_pulse[2]),
            self.o_data_strobe.eq(sync_pulse[5]),
            If(sync_pulse[0],
                NextState('SEND_DATA'),
                # XXX Fix this so that it doesn't glitch
                # NextValue(fit_dat, shifter.o_data & ~bitstuff.o_stall),
            ),
        )
        tx_pipeline_fsm.act('SEND_DATA',
            self.o_data_strobe.eq(shifter.o_get & ~stall & self.i_oe),
            fit_dat.eq(shifter.o_data & ~bitstuff.o_stall),
            fit_oe.eq(1),
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
                NextState('IDLE')
            ),
        )

        self.comb += [
            bitstuff.i_data.eq(shifter.o_data),
            bitstuff.reset.eq(reset_bitstuff),
            stall.eq(bitstuff.o_stall),
        ]

        # 48MHz domain
        # NRZI encoding
        # nrzi_dat = Signal()
        # nrzi_oe = Signal()
        # Cross the data from the 12MHz domain to the 48MHz domain
        # cdc_dat = cdc.MultiReg(fit_dat, nrzi_dat, odomain="usb_48", n=3)
        # cdc_oe  = cdc.MultiReg(fit_oe, nrzi_oe, odomain="usb_48", n=3)
        # self.specials += [cdc_dat, cdc_oe]

        self.comb += [
            nrzi.i_valid.eq(self.i_bit_strobe),
            nrzi.i_data.eq(fit_dat),
            nrzi.i_oe.eq(fit_oe),

            self.o_usbp.eq(nrzi.o_usbp),
            self.o_usbn.eq(nrzi.o_usbn),
            self.o_oe.eq(nrzi.o_oe),
        ]