#!/usr/bin/env python3

import functools
import operator
import unittest

from migen import *

from migen.fhdl.decorators import CEInserter, ResetInserter

from ..test.common import BaseUsbTestCase
from ..utils.CrcMoose3 import CrcAlgorithm
from ..utils.bits import *
from ..utils.packet import crc5, crc16, encode_data, b
from .shifter import TxShifter
from .tester import module_tester

from .crc import TxSerialCrcGenerator, TxParallelCrcGenerator, TxCrcPipeline, bytes_to_int


@module_tester(
    TxSerialCrcGenerator,

    width       = None,
    polynomial  = None,
    initial     = None,

    reset       = (1,),
    ce          = (1,),
    i_data      = (1,),

    o_crc       = ("width",)
)
class TestTxSerialCrcGenerator(BaseUsbTestCase):
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
            polynomial = 0b00101, # polynomial = (5, 2, 0)
            initial    = 0b11111, # seed = 0x1F

            reset      = "-_____________________________",
            ce         = "_-___-___-___-___-___------___",
            i_data     = " 0   1   111101110111000011   ",
            o_crc      = "                           ccc"
        )

    def test_data_crc16_nonzero(self):
        self.do(
            width      = 16,
            polynomial = 0b1000000000000101, # polynomial = (16, 15, 2, 0)
            initial    = 0b1111111111111111, # seed = 0xFFFF

            reset      = "-________________________________________________________________________",
            ce         = "_--------_--------_--------_--------_--------_--------_--------_--------_",
            i_data     = " 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 ",
            o_crc      =("                                                                        *", [0x94dd])
        )


class TestTxParallelCrcGenerator(BaseUsbTestCase):
    def sim(self, name, dut, in_data, expected_crc):
        def stim():
            yield dut.i_data_strobe.eq(1)
            for d in in_data:
                yield dut.i_data_payload.eq(d)
                yield
                o_crc = yield dut.o_crc
                print("{0} {1:04x} {1:016b} {2:04x} {2:016b}".format(name, expected_crc, o_crc))
            yield
            o_crc = yield dut.o_crc
            print("{0} {1:04x} {1:016b} {2:04x} {2:016b}".format(name, expected_crc, o_crc))
            self.assertEqual(hex(expected_crc), hex(o_crc))

        run_simulation(dut, stim(), vcd_name=self.make_vcd_name())

    def sim_crc16(self, in_data):
        expected_crc = bytes_to_int(crc16(in_data))
        dut = TxParallelCrcGenerator(
            crc_width  = 16,
            polynomial = 0b1000000000000101,
            initial    = 0b1111111111111111,
            data_width = 8,
        )
        mask = 0xff
        self.assertSequenceEqual(in_data, [x & mask for x in in_data])
        self.sim("crc16", dut, in_data, expected_crc)

    def sim_crc5(self, in_data):
        expected_crc = crc5(in_data)
        dut = TxParallelCrcGenerator(
            crc_width  = 5,
            polynomial = 0b00101,
            initial    = 0b11111,
            data_width = 4,
        )
        mask = 0x0f
        self.assertSequenceEqual(in_data, [x & mask for x in in_data])
        self.sim("crc5", dut, in_data, expected_crc)

    def test_token_crc5_zeroes(self):
        self.sim_crc5([0, 0])

    def test_token_crc5_nonzero1(self):
        self.sim_crc5([b("0110"), b("0000")])

    def test_data_crc16_nonzero1(self):
        self.sim_crc16([
            b("00000001"), b("01100000"), b("00000000"), b("10000000"),
            b("00000000"), b("00000000"), b("00000010"), b("00000000"),
        ])

    def test_data_crc16_nonzero2(self):
        self.sim_crc16([
            0b00000001, 0b01100000, 0b00000000, 0b10000000,
            0b00000000, 0b00000000, 0b00000010, 0b00000000,
        ])


class TestCrcPipeline(BaseUsbTestCase):
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

        run_simulation(dut, stim(), vcd_name=self.make_vcd_name())

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

    def test_sequential_bytes2(self):
        self.sim([0, 1])

    def test_sequential_bytes3(self):
        self.sim([1, 0])


if __name__ == "__main__":
    import doctest
    doctest.testmod()
    unittest.main()
