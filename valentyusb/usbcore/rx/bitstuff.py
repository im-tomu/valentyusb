#!/usr/bin/env python3

from migen import *
from migen.fhdl.decorators import ResetInserter

from ..test.common import BaseUsbTestCase
import unittest


@ResetInserter()
class RxBitstuffRemover(Module):
    """RX Bitstuff Removal

    Long sequences of 1's would cause the receiver to lose it's lock on the
    transmitter's clock.  USB solves this with bitstuffing.  A '0' is stuffed
    after every 6 consecutive 1's.  This extra bit is required to recover the
    clock, but it should not be passed on to higher layers in the device.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.9
    https://en.wikipedia.org/wiki/Bit_stuffing

    Clock Domain
    ------------
    usb_12 : 12MHz

    Input Ports
    ------------
    i_valid : Signal(1)
        Qualifier for all of the input signals.  Indicates one bit of valid
        data is present on the inputs.

    i_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    Output Ports
    ------------
    o_data : Signal(1)
        Decoded data bit from USB bus.

    o_stall : Signal(1)
        Indicates the bit stuffer just removed an extra bit, so no data available.

    o_error : Signal(1)
        Indicates there has been a bitstuff error. A bitstuff error occurs
        when there should be a stuffed '0' after 6 consecutive 1's; but instead
        of a '0', there is an additional '1'.  This is normal during IDLE, but
        should never happen within a packet.
        Qualified by valid.
    """

    def __init__(self):
        self.i_valid = Signal()
        self.i_data = Signal()

        # This state machine recognizes sequences of 6 bits and drops the 7th
        # bit.  The fsm implements a counter in a series of several states.
        # This is intentional to help absolutely minimize the levels of logic
        # used.
        self.submodules.stuff = stuff = FSM(reset_state="D0")

        drop_bit = Signal(1)

        for i in range(6):
            stuff.act("D%d" % i,
                If(self.i_valid,
                    If(self.i_data,
                        # Receiving '1' increments the bitstuff counter.
                        NextState("D%d" % (i + 1))
                    ).Else(
                        # Receiving '0' resets the bitstuff counter.
                        NextState("D0")
                    )
                ),
            )

        stuff.act("D6",
            If(self.i_valid,
                drop_bit.eq(1),
                # Reset the bitstuff counter, drop the data.
                NextState("D0")
            )
        )

        # pass all of the outputs through a pipe stage
        self.o_data = Signal()
        self.o_error = Signal()
        self.o_stall = Signal(reset=1)

        self.sync += [
            self.o_data.eq(self.i_data),
            self.o_stall.eq(drop_bit | ~self.i_valid),
            self.o_error.eq(drop_bit & self.i_data & self.i_valid),
        ]
