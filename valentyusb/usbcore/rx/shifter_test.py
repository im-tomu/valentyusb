#!/usr/bin/env python3

import unittest

from migen import *
from migen.fhdl.decorators import CEInserter, ResetInserter

from ..test.common import BaseUsbTestCase

from .shifter import RxShifter


class TestRxShifter(BaseUsbTestCase):
    def shifter_test(self, vector, short_name):
        actual_output = []
        def send(reset, data , put=None, output=None):
            for i in range(len(data)+2):
                if i < len(data):
                    if data[i] == '|':
                        assert reset[i] == '|', reset[i]
                        assert put[i]   == '|', put[i]
                    yield dut.i_valid.eq(data[i] != '|')
                    yield dut.reset.eq(reset[i] == '-')
                    yield dut.i_data.eq(data[i] == '1')
                yield
                o_put = yield dut.o_put
                if o_put:
                    last_output = yield dut.o_data
                    actual_output.append(last_output)

        with self.subTest(short_name=short_name, vector=vector):
            dut = RxShifter(8)

            actual_output.clear()
            run_simulation(
                dut,
                send(**vector),
                vcd_name=self.make_vcd_name(testsuffix=short_name),
            )
            self.assertListEqual(vector['output'], actual_output)

    def test_basic_shift_in(self):
        return self.shifter_test(
            # 0
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "1|00000000|",
                put      = "_|_______-|",
                output   = [0b00000000]
            ), "basic-shift-in")

    def test_basic_shift_in_1(self):
        return self.shifter_test(
            # 1
            dict(
                # basic shift in
                reset    = "-|________|||________|",
                data     = "1|00000001|||00000001|",
                put      = "_|_______-|||_______-|",
                output   = [0b00000001,0b00000001]
            ), "basic-shift-in-1")

    def test_basic_shift_in_2(self):
        return self.shifter_test(
            # 2
            dict(
                # basic shift in
                reset    = "-|________|||________|",
                data     = "1|10000000|||10000000|",
                put      = "_|_______-|||_______-|",
                output   = [0b10000000,0b10000000]
            ), "basic-shift-in-2")

    def test_basic_shift_in_3(self):
        return self.shifter_test(
            # 3
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "1|11111111|",
                put      = "_|_______-|",
                output   = [0b11111111]
            ), "basic-shift-in-3")

    def test_basic_shift_in_4(self):
        return self.shifter_test(
            # 4
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "1|10000000|",
                put      = "_|_______-|",
                output   = [0b10000000]
            ), "basic-shift-in-4")

    def test_basic_shift_in_5(self):
        return self.shifter_test(
            # 5
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "1|00000001|",
                put      = "_|_______-|",
                output   = [0b00000001]
            ), "basic-shift-in-5")

    def test_basic_shift_in_6(self):
        return self.shifter_test(
            # 6
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "1|01111110|",
                put      = "_|_______-|",
                output   = [0b01111110]
            ), "basic-shift-in-6")

    def test_basic_shift_in_7(self):
        return self.shifter_test(
            # 7
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "0|01110100|",
                put      = "_|_______-|",
                output   = [0b01110100]
            ), "basic-shift-in-7")

    def test_basic_shift_in_2_bytes(self):
        return self.shifter_test(
            # 8
            dict(
                # basic shift in, 2 bytes
                reset    = "-|________|||________|",
                data     = "0|01110100|||10101000|",
                put      = "_|_______-|||_______-|",
                output   = [0b01110100,0b10101000]
            ), "basic-shift-in-2-bytes")

    def test_multiple_resets(self):
        return self.shifter_test(
            # 9
            dict(
                # multiple resets
                reset    = "-|________|______-|___-|________|",
                data     = "0|01110100|0011010|0111|10101000|",
                put      = "_|_______-|_______|____|_______-|",
                output   = [0b01110100,           0b10101000]
            ), "multiple-resets")

    def test_multiple_resets_tight_timing(self):
        return self.shifter_test(
            # 10
            dict(
                # multiple resets (tight timing)
                reset    = "-|________|-|________|",
                data     = "0|01110100|1|00101000|",
                put      = "_|_______-|_|_______-|",
                output   = [0b01110100,0b00101000]
            ), "multiple-resets-tight-timing")

if __name__ == "__main__":
    unittest.main()
