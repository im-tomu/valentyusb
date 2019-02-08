#!/usr/bin/env python3

from migen import *
from migen.fhdl.decorators import ResetInserter

from ..test.common import BaseUsbTestCase
import unittest


@ResetInserter()
class RxCrcChecker(Module):
    """CRC Checker

    Checks the CRC of a serial stream of data.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 8.3.5
    https://en.wikipedia.org/wiki/Cyclic_redundancy_check

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Width of the CRC.

    polynomial : int
        CRC polynomial in integer form.

    initial : int
        Initial value of the CRC register before data starts shifting in.

    residual : int
        Value of the CRC register if all the shifted in data is valid.

    Input Ports
    ------------
    i_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    i_reset : Signal(1)
        Resets the CRC calculation back to the initial state.

    i_valid : Signal(1)
        Indicate that i_data is valid and a CRC should be calculated

    Output Ports
    ------------
    o_crc_good : Signal()
        CRC value is good.
    """
    def __init__(self, width, polynomial, initial, residual):
        self.i_data = Signal()
        self.i_reset = Signal()
        self.i_valid = Signal()

        crc = Signal(width)
        crc_good = Signal(1)
        crc_invert = Signal(1)

        self.comb += [
            crc_good.eq(crc == residual),
            crc_invert.eq(self.i_data ^ crc[width - 1])
        ]

        for i in range(width):
            rhs = None
            if i == 0:
                rhs = crc_invert
            else:
                if (polynomial >> i) & 1:
                    rhs = crc[i - 1] ^ crc_invert
                else:
                    rhs = crc[i - 1]

            self.sync += [
                If(self.i_reset,
                    crc[i].eq((initial >> i) & 1)
                ).Elif(self.i_valid,
                    crc[i].eq(rhs)
                )
            ]

        # flop all outputs
        self.o_crc_good = Signal(1)

        self.sync += [
            self.o_crc_good.eq(crc_good)
        ]
