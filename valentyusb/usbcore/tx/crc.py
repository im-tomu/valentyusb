#!/usr/bin/env python3

import unittest

from migen import *

from migen.fhdl.decorators import CEInserter, ResetInserter


from .tester import module_tester


@CEInserter()
@ResetInserter()
class TxCrcGenerator(Module):
    """
    Transmit CRC Generator

    TxCrcGenerator generates a running CRC.

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

    Input Ports
    ------------
    i_data : Signal(1)
        Serial data to generate CRC for.
        Qualified by i_shift.

    Output Ports
    ------------
    o_crc : Signal(width)
        Current CRC value.

    """
    def __init__(self, width, polynomial, initial):

        self.i_data = Signal()

        crc = Signal(width, reset=initial)
        crc_invert = Signal(1)

        self.comb += [
            crc_invert.eq(self.i_data ^ crc[width - 1])
        ]

        for i in range(width):
            rhs_data = None
            if i == 0:
                rhs_data = crc_invert
            else:
                if (polynomial >> i) & 1:
                    rhs_data = crc[i - 1] ^ crc_invert
                else:
                    rhs_data = crc[i - 1]

            self.sync += [
                crc[i].eq(rhs_data)
            ]

        self.o_crc = Signal(width)

        for i in range(width):
            self.comb += [
                self.o_crc[i].eq(1 ^ crc[width - i - 1]),
            ]


@module_tester(
    TxCrcGenerator,

    width       = None,
    polynomial  = None,
    initial     = None,

    reset       = (1,),
    ce          = (1,),
    i_data      = (1,),

    o_crc       = ("width",)
)
class TestTxCrcGenerator(unittest.TestCase):
    def test_token_crc5_zeroes(self):
        self.do(
            width      = 5,
            polynomial = 0b00101,
            initial    = 0b11111,

            reset      = "-_______________",
            ce         = "__-----------___",
            i_data     = "  00000000000   ",
            o_crc      = "             222"
        )

    def test_token_crc5_zeroes_alt(self):
        self.do(
            width      = 5,
            polynomial = 0b00101,
            initial    = 0b11111,

            reset      = "-______________",
            ce         = "_-----------___",
            i_data     = " 00000000000   ",
            o_crc      = "            222"
        )

    def test_token_crc5_nonzero(self):
        self.do(
            width      = 5,
            polynomial = 0b00101,
            initial    = 0b11111,

            reset      = "-______________",
            ce         = "_-----------___",
            i_data     = " 01100000011   ",
            o_crc      = "            ccc"
        )

    def test_token_crc5_nonzero_stall(self):
        self.do(
            width      = 5,
            polynomial = 0b00101,
            initial    = 0b11111,

            reset      = "-_____________________________",
            ce         = "_-___-___-___-___-___------___",
            i_data     = " 0   1   111101110111000011   ",
            o_crc      = "                           ccc"
        )

    def test_data_crc16_nonzero(self):
        self.do(
            width      = 16,
            polynomial = 0b1000000000000101,
            initial    = 0b1111111111111111,

            reset      = "-________________________________________________________________________",
            ce         = "_--------_--------_--------_--------_--------_--------_--------_--------_",
            i_data     = " 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 ",
            o_crc      =("                                                                        *", [0x94dd])
        )


if __name__ == "__main__":
    unittest.main()
