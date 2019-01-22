#!/usr/bin/env python3

import unittest

from migen import *

from .tester import module_tester
from ..test.common import BaseUsbTestCase


class TxNRZIEncoder(Module):
    """
    NRZI Encode

    In order to ensure there are enough bit transitions for a receiver to recover
    the clock usb uses NRZI encoding.  This module processes the incoming
    dj, dk, se0, and valid signals and decodes them to data values.  It
    also pipelines the se0 signal and passes it through unmodified.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.8
    https://en.wikipedia.org/wiki/Non-return-to-zero

    Clock Domain
    ------------
    usb_48 : 48MHz

    Input Ports
    -----------
    i_valid : Signal(1)
        Qualifies oe, data, and se0.

    i_oe : Signal(1)
        Indicates that the transmit pipeline should be driving USB.

    i_data : Signal(1)
        Data bit to be transmitted on USB. Qualified by o_valid.

    i_se0 : Signal(1)
        Overrides value of o_data when asserted and indicates that SE0 state
        should be asserted on USB. Qualified by o_valid.

    Output Ports
    ------------
    o_usbp : Signal(1)
        Raw value of USB+ line.

    o_usbn : Signal(1)
        Raw value of USB- line.

    o_oe : Signal(1)
        When asserted it indicates that the tx pipeline should be driving USB.
    """

    def __init__(self):
        self.i_valid = Signal()
        self.i_oe = Signal()
        self.i_data = Signal()

        # Simple state machine to perform NRZI encoding.
        self.submodules.nrzi = nrzi = FSM()

        usbp = Signal(1)
        usbn = Signal(1)
        oe = Signal(1)

        # wait for new packet to start
        nrzi.act("IDLE",
            usbp.eq(1),
            usbn.eq(0),
            oe.eq(0),

            If(self.i_valid,
                If(self.i_oe,
                    # first bit of sync always forces a transition, we idle
                    # in J so the first output bit is K.
                    NextState("DK")
                )
            )
        )

        # the output line is in state J
        nrzi.act("DJ",
            usbp.eq(1),
            usbn.eq(0),
            oe.eq(1),

            If(self.i_valid,
                If(~self.i_oe,
                    NextState("SE0A")
                ).Elif(self.i_data,
                    NextState("DJ")
                ).Else(
                    NextState("DK")
                )
            )
        )

        # the output line is in state K
        nrzi.act("DK",
            usbp.eq(0),
            usbn.eq(1),
            oe.eq(1),

            If(self.i_valid,
                If(~self.i_oe,
                    NextState("SE0A")
                ).Elif(self.i_data,
                    NextState("DK")
                ).Else(
                    NextState("DJ")
                )
            )
        )

        # first bit of the SE0 state
        nrzi.act("SE0A",
            usbp.eq(0),
            usbn.eq(0),
            oe.eq(1),

            If(self.i_valid,
                NextState("SE0B")
            )
        )
        # second bit of the SE0 state
        nrzi.act("SE0B",
            usbp.eq(0),
            usbn.eq(0),
            oe.eq(1),

            If(self.i_valid,
                NextState("EOPJ")
            )
        )

        # drive the bus back to J before relinquishing control
        nrzi.act("EOPJ",
            usbp.eq(1),
            usbn.eq(0),
            oe.eq(1),

            If(self.i_valid,
                NextState("IDLE")
            )
        )

        # flop all outputs
        self.o_usbp = Signal(1)
        self.o_usbn = Signal(1)
        self.o_oe = Signal(1)

        self.sync += [
            self.o_oe.eq(oe),
            self.o_usbp.eq(usbp),
            self.o_usbn.eq(usbn),
        ]
