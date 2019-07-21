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

        da_reset_shifter = Signal()
        da_reset_bitstuff = Signal() # Need to reset the bit stuffer 1 cycle after the shifter.
        stall = Signal()

        # These signals are set during the sync pulse
        sp_reset_bitstuff = Signal()
        sp_reset_shifter = Signal()
        sp_bit = Signal()
        sp_o_data_strobe = Signal()

        # 12MHz domain

        da_stalled_reset = Signal()
        bitstuff_valid_data = Signal()

        # Keep a Gray counter around to smoothly transition between states
        state_gray = Signal(2)
        state_data = Signal()
        state_sync = Signal()

        self.comb += [
            shifter.i_data.eq(self.i_data_payload),
            # Send a data strobe when we're two bits from the end of the sync pulse.
            # This is because the pipeline takes two bit times, and we want to ensure the pipeline
            # has spooled up enough by the time we're there.

            shifter.reset.eq(da_reset_shifter | sp_reset_shifter),
            shifter.ce.eq(~stall),

            bitstuff.reset.eq(da_reset_bitstuff),
            bitstuff.i_data.eq(shifter.o_data),
            stall.eq(bitstuff.o_stall),

            sp_bit.eq(sync_pulse[0]),
            sp_reset_bitstuff.eq(sync_pulse[0]),

            # The shifter has one clock cycle of latency, so reset it
            # one cycle before the end of the sync byte.
            sp_reset_shifter.eq(sync_pulse[1]),

            sp_o_data_strobe.eq(sync_pulse[5]),

            state_data.eq(state_gray[0] & state_gray[1]),
            state_sync.eq(state_gray[0] & ~state_gray[1]),

            fit_oe.eq(state_data | state_sync),
            fit_dat.eq((state_data & shifter.o_data & ~bitstuff.o_stall) | sp_bit),
            self.o_data_strobe.eq((state_data & shifter.o_get & ~stall & self.i_oe) | sp_o_data_strobe),
        ]

        # If we reset the shifter, then o_empty will go high on the next cycle.
        #

        self.sync.usb_12 += [
            # If the shifter runs out of data, percolate the "reset" signal to the
            # shifter, and then down to the bitstuffer.
            # da_reset_shifter.eq(~stall & shifter.o_empty & ~da_stalled_reset),
            # da_stalled_reset.eq(da_reset_shifter),
            # da_reset_bitstuff.eq(~stall & da_reset_shifter),
            bitstuff_valid_data.eq(~stall & shifter.o_get & self.i_oe),
        ]

        tx_pipeline_fsm.act('IDLE',
            If(self.i_oe,
                NextState('SEND_SYNC'),
                NextValue(sync_pulse, 1 << 7),
                NextValue(state_gray, 0b01),
            ).Else(
                NextValue(state_gray, 0b00),
            )
        )

        tx_pipeline_fsm.act('SEND_SYNC',
            NextValue(sync_pulse, sync_pulse >> 1),

            If(sync_pulse[0],
                NextState('SEND_DATA'),
                NextValue(state_gray, 0b11),
            ).Else(
                NextValue(state_gray, 0b01),
            ),
        )

        tx_pipeline_fsm.act('SEND_DATA',
            If(~self.i_oe & shifter.o_empty & ~bitstuff.o_stall,
                If(bitstuff.o_will_stall,
                    NextState('STUFF_LAST_BIT')
                ).Else(
                    NextValue(state_gray, 0b10),
                    NextState('IDLE'),
                )
            ).Else(
                NextValue(state_gray, 0b11),
            ),
        )

        tx_pipeline_fsm.act('STUFF_LAST_BIT',
            NextValue(state_gray, 0b10),
            NextState('IDLE'),
        )

        # 48MHz domain
        # NRZI encoding
        nrzi_dat = Signal()
        nrzi_oe = Signal()
        # Cross the data from the 12MHz domain to the 48MHz domain
        cdc_dat = cdc.MultiReg(fit_dat, nrzi_dat, odomain="usb_48", n=3)
        cdc_oe  = cdc.MultiReg(fit_oe, nrzi_oe, odomain="usb_48", n=3)
        self.specials += [cdc_dat, cdc_oe]

        self.comb += [
            nrzi.i_valid.eq(self.i_bit_strobe),
            nrzi.i_data.eq(nrzi_dat),
            nrzi.i_oe.eq(nrzi_oe),

            self.o_usbp.eq(nrzi.o_usbp),
            self.o_usbn.eq(nrzi.o_usbn),
            self.o_oe.eq(nrzi.o_oe),
        ]
