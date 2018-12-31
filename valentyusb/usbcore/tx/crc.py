#!/usr/bin/env python3

import unittest

from migen import *

from migen.fhdl.decorators import CEInserter, ResetInserter

from ..utils.packet import crc16, encode_data
from .shifter import TxShifter
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


class TxCrcPipeline(Module):
    def __init__(self):
        self.i_data_payload = Signal(8)
        self.o_data_ack = Signal()
        self.o_crc16 = Signal(16)

        self.reset = reset = Signal()
        reset_n1 = Signal()
        reset_n2 = Signal()
        self.ce = ce = Signal()

        self.sync += [
            reset_n2.eq(reset_n1),
            reset_n1.eq(reset),
        ]

        self.submodules.shifter = shifter = TxShifter(width=8)
        self.comb += [
            shifter.i_data.eq(self.i_data_payload),
            shifter.reset.eq(reset),
            shifter.ce.eq(ce),
            self.o_data_ack.eq(shifter.o_get),
        ]

        self.submodules.crc = crc_calc = TxCrcGenerator(
            width      = 16,
            polynomial = 0b1000000000000101,
            initial    = 0b1111111111111111,
        )
        self.comb += [
            crc_calc.i_data.eq(shifter.o_data),
            crc_calc.reset.eq(reset_n2),
            crc_calc.ce.eq(ce),
            self.o_crc16.eq(crc_calc.o_crc),
        ]


class TestCrcPipeline(unittest.TestCase):
    maxDiff=None

    def sim(self, data):
        expected_crc = crc16(data)

        dut = TxCrcPipeline()
        dut.expected_crc = Signal(16)
        def stim():
            MAX = 1000
            yield dut.expected_crc[:8].eq(expected_crc[0])
            yield dut.expected_crc[8:].eq(expected_crc[1])
            yield dut.reset.eq(1)
            yield dut.ce.eq(1)
            for i in range(MAX+1):
                if i > 10:
                    yield dut.reset.eq(0)

                ack = yield dut.o_data_ack
                if ack:
                    if len(data) == 0:
                        yield dut.ce.eq(0)
                        for i in range(5):
                            yield
                        crc16_value = yield dut.o_crc16

                        encoded_expected_crc = encode_data(expected_crc)
                        encoded_actual_crc = encode_data([crc16_value & 0xff, crc16_value >> 8])
                        self.assertSequenceEqual(encoded_expected_crc, encoded_actual_crc)
                        return
                    data.pop(0)
                if len(data) > 0:
                    yield dut.i_data_payload.eq(data[0])
                else:
                    yield dut.i_data_payload.eq(0xff)
                yield
            self.assertLess(i, MAX)

        run_simulation(dut, stim(), vcd_name="vcd/test_crc_pipeline_%s.vcd" % self.id())

    def test_00000001_byte(self):
        self.sim([0b00000001])

    def test_10000000_byte(self):
        self.sim([0b10000000])

    def test_00000000_byte(self):
        self.sim([0])

    def test_11111111_byte(self):
        self.sim([0xff])

    def test_10101010_byte(self):
        self.sim([0b10101010])

    def test_zero_bytes(self):
        self.sim([0, 0, 0])

    def test_sequential_bytes(self):
        self.sim([0, 1, 2])


if __name__ == "__main__":
    unittest.main()
