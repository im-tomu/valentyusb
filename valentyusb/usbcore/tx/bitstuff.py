#!/usr/bin/env python3

import unittest

from migen import *

from migen.fhdl.decorators import CEInserter, ResetInserter

from .tester import module_tester
from ..test.common import BaseUsbTestCase


@ResetInserter()
class TxBitstuffer(Module):
    """
    Bitstuff Insertion

    Long sequences of 1's would cause the receiver to lose it's lock on the
    transmitter's clock.  USB solves this with bitstuffing.  A '0' is stuffed
    after every 6 consecutive 1's.

    The TxBitstuffer is the only component in the transmit pipeline that can
    delay transmission of serial data.  It is therefore responsible for
    generating the bit_strobe signal that keeps the pipe moving forward.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.9
    https://en.wikipedia.org/wiki/Bit_stuffing

    Clock Domain
    ------------
    usb_12 : 48MHz

    Input Ports
    ------------
    i_data : Signal(1)
        Data bit to be transmitted on USB.

    Output Ports
    ------------
    o_data : Signal(1)
        Data bit to be transmitted on USB.

    o_stall : Signal(1)
        Used to apply backpressure on the tx pipeline.
    """
    def __init__(self):
        self.i_data = Signal()

        self.o_stall = Signal(1)
        self.o_will_stall = Signal(1)
        self.o_data = Signal(1)

        self.submodules.stuff = stuff = FSM()

        stuff_bit = Signal(1)

        for i in range(5):
            stuff.act("D%d" % i,
                If(self.i_data,
                    # Receiving '1' increments the bitstuff counter.
                    NextState("D%d" % (i + 1))
                ).Else(
                    # Receiving '0' resets the bitstuff counter.
                    NextState("D0")
                )
            )

        stuff.act("D5",
            If(self.i_data,
                # There's a '1', so indicate we might stall on the next loop.
                self.o_will_stall.eq(1),

                # Receiving '1' increments the bitstuff counter.
                NextState("D6")
            ).Else(
                # Receiving '0' resets the bitstuff counter.
                NextState("D0")
            )
        )

        stuff.act("D6",
            # stuff a bit
            stuff_bit.eq(1),

            # Reset the bitstuff counter
            NextState("D0")
        )

        self.comb += [
            self.o_stall.eq(stuff_bit)
        ]

        # flop outputs
        self.sync += [
            If(stuff_bit,
                self.o_data.eq(0),
            ).Else(
                self.o_data.eq(self.i_data),
            ),
        ]
