#!/usr/bin/env python3

from migen import *
from migen.fhdl.decorators import CEInserter, ResetInserter
from ..test.common import BaseUsbTestCase

import unittest

@CEInserter()
@ResetInserter()
class RxShifter(Module):
    """RX Shifter

    A shifter is responsible for shifting in serial bits and presenting them
    as parallel data.  The shifter knows how many bits to shift and has
    controls for resetting the shifter.

    Clock Domain
    ------------
    usb_12 : 12MHz

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Number of bits to shift in.

    Input Ports
    -----------
    i_data : Signal(1)
        Serial input data.

    i_valid : Signal(1)
        Indicates i_data contains a valid bit

    Output Ports
    ------------
    o_data : Signal(width)
        Shifted in data.

    o_put : Signal(1)
        Asserted for one clock once the register is full.
    """
    def __init__(self, width):
        self.i_data = Signal()
        self.i_valid = Signal()

        self.o_data = Signal(width)
        self.o_put = Signal()

        # Instead of using a counter, we will use a sentinel bit in the shift
        # register to indicate when it is full.
        shift_reg = Signal(width+1, reset=0b1)

        self.sync += [
            If(shift_reg[width],
                self.o_put.eq(1),
                self.o_data.eq(shift_reg[0:width]),
                If(self.i_valid,
                    shift_reg.eq(Cat(self.i_data, shift_reg.reset[0:width])),
                ).Else(
                    shift_reg.eq(shift_reg.reset[0:width])
                )
            ).Elif(self.i_valid,
                self.o_put.eq(0),
                shift_reg.eq(Cat(self.i_data, shift_reg[0:width])),
            ).Else(
                self.o_put.eq(0),
            ),
        ]
