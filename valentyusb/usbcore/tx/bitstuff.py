#!/usr/bin/env python3

import unittest

from migen import *

from .tester import module_tester


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

        self.submodules.stuff = stuff = FSM()

        stuff_bit = Signal(1)

        for i in range(6):
            stuff.act("D%d" % i,
                If(self.i_data,
                    # Receiving '1' increments the bitstuff counter.
                    NextState("D%d" % (i + 1))
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

        self.o_stall = Signal(1)
        self.o_data = Signal(1)

        self.comb += [
            self.o_stall.eq(stuff_bit)
        ]

        # flop outputs
        self.sync += [
            self.o_data.eq(self.i_data & ~stuff_bit),
        ]


@module_tester(
    TxBitstuffer,

    i_data      = (1,),

    o_stall     = (1,),
    o_data      = (1,),
)
class TestTxBitstuffer(unittest.TestCase):
    def test_passthrough(self):
        self.do(
            i_data  = "--___---__",

            o_stall = "__________",
            o_data  = "_--___---_",
        )

    def test_passthrough_se0(self):
        self.do(
            i_data  = "--___---__",

            o_stall = "__________",
            o_data  = "_--___---_",
        )

    def test_bitstuff(self):
        self.do(
            i_data  = "---------__",

            o_stall = "______-____",
            o_data  = "_------_--_",
        )

    def test_bitstuff_input_stall(self):
        self.do(
            i_data  = "---------",

            o_stall = "______-__",
            o_data  = "_------_-",
        )

    def test_bitstuff_se0(self):
        self.do(
            i_data  = "---------__-",

            o_stall = "______-_____",
            o_data  = "_------_--__",
        )

    def test_bitstuff_at_eop(self):
        self.do(
            i_data  = "-------__",

            o_stall = "______-__",
            o_data  = "_------__",
        )

    def test_multi_bitstuff(self):
        self.do(
            i_data  = "----------------",

            o_stall = "______-______-__",
            o_data  = "_------_------_-",
        )


if __name__ == "__main__":
    unittest.main()
