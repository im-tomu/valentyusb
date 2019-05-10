#!/usr/bin/env python3

import unittest

from migen import *

from migen.fhdl.decorators import CEInserter, ResetInserter

from ..utils.packet import b
from ..test.common import BaseUsbTestCase

from .shifter import TxShifter


class TestTxShifter(BaseUsbTestCase):
    def shifter_test(self, vector, name):
        def send(reset, ce, data):
            out = ""
            get = ""
            empty = ""

            yield dut.i_data.eq(data.pop(0))
            for i in range(len(ce)+1):
                if i < len(ce):
                    if ce[i] == "|":
                        continue
                    yield dut.reset.eq(reset[i] == '-')
                    yield dut.ce.eq(ce[i] == '-')

                yield

                if i < 1:
                    continue

                out += str((yield dut.o_data))
                o_get = yield dut.o_get
                o_empty = yield dut.o_empty
                get += {
                    0: "_",
                    1: "-",
                }[o_get]
                empty += {
                    0: "_",
                    1: "-",
                }[o_empty]

                if o_get:
                    if data:
                        yield dut.i_data.eq(data.pop(0))
                    else:
                        yield dut.i_data.eq(0)

                if ce[i-1] == "|":
                    out += "|"
                    get += "|"
                    empty += "|"

            return out, empty, get

        def stim(width, data, reset, ce, out, empty, get):
            actual_out, actual_empty, actual_get = yield from send(reset, ce, data)
            self.assertSequenceEqual(out, actual_out)
            self.assertSequenceEqual(empty, actual_empty)
            self.assertSequenceEqual(get, actual_get)

        with self.subTest(name=name, vector=vector):
            fname = name.replace(' ', '_')
            dut = TxShifter(vector["width"])

            run_simulation(dut, stim(**vector),
                vcd_name=self.make_vcd_name(testsuffix=fname))

    def test_basic_shift_out_1(self):
        return self.shifter_test(
            dict(
                width = 8,
                data  = [b("00000001"), b("00000001"), b("00000001"), 0],
                reset = "-|________|________|________",
                ce    = "-|--------|--------|--------",
                out   = "0|00000001|00000001|00000001",
                empty = "-|_______-|_______-|_______-",
                get   = "_|-_______|-_______|-_______",
            ), "basic shift out 1")

    def test_basic_shift_out_2(self):
        return self.shifter_test(
            dict(
                width = 8,
                data  = [b("10000000"), b("10000000"), b("10000000"), 0],
                reset = "-|________|________|________",
                ce    = "-|--------|--------|--------",
                out   = "0|10000000|10000000|10000000",
                empty = "-|_______-|_______-|_______-",
                get   = "_|-_______|-_______|-_______",
            ), "basic shift out 2")

    def test_basic_shift_out_3(self):
        return self.shifter_test(
            dict(
                width = 8,
                data  = [b("01100110"), b("10000001"), b("10000000"), 0],
                reset = "-|________|________|________",
                ce    = "-|--------|--------|--------",
                out   = "0|01100110|10000001|10000000",
                empty = "-|_______-|_______-|_______-",
                get   = "_|-_______|-_______|-_______",
            ), "basic shift out 3")

    def test_stall_shift_out_1(self):
        return self.shifter_test(
            dict(
                width = 8,
                data  = [b("00000001"), b("00000001"), b("00000001"), 0],
                reset = "-|_________|________|________",
                ce    = "-|--_------|--------|--------",
                out   = "0|000000001|00000001|00000001",
                empty = "-|________-|_______-|_______-",
                get   = "_|-________|-_______|-_______",
            ), "stall shift out 1")

    def test_stall_shift_out_2(self):
        return self.shifter_test(
            dict(
                width = 8,
                data  = [b("10000000"), b("10000000"), b("10000000"), 0],
                reset = "-|_________|________|________",
                ce    = "-|---_-----|--------|--------",
                out   = "0|100000000|10000000|10000000",
                empty = "-|________-|_______-|_______-",
                get   = "_|-________|-_______|-_______",
            ), "stall shift out 2")

    def test_stall_shift_out_3(self):
        return self.shifter_test(
            dict(
                width = 8,
                data  = [b("01100110"), b("10000001"), b("10000000"), 0],
                reset = "-|_________|________|________",
                ce    = "-|---_-----|--------|--------",
                out   = "0|011100110|10000001|10000000",
                empty = "-|________-|_______-|_______-",
                get   = "_|-________|-_______|-_______",
            ), "stall shift out 3")

    def test_multistall_shift_out_1(self):
        return self.shifter_test(
            dict(
                width = 8,
                data  = [b("00000001"), b("00000001"), b("00000001"), 0],
                reset = "-|___________|_________|_________",
                ce    = "-|--___------|--------_|----_----",
                out   = "0|00000000001|000000011|000000001",
                empty = "-|__________-|_______--|________-",
                get   = "_|-__________|-________|-________",
            ), "mutlistall shift out 1")

    def test_multistall_shift_out_2(self):
        return self.shifter_test(
            dict(
                width = 8,
                data  = [b("10000000"), b("10000000"), b("10000000"), 0],
                reset = "-|____________|________|__________",
                ce    = "-|---____-----|--------|-_----_---",
                out   = "0|100000000000|10000000|1100000000",
                empty = "-|___________-|_______-|_________-",
                get   = "_|-___________|-_______|--________",
            ), "mutlistall shift out 2")

    def test_multistall_shift_out_3(self):
        return self.shifter_test(
            dict(
                width = 8,
                data  = [b("01100110"), b("10000001"), b("10000000"), 0],
                reset = "-|____________|___________|_________",
                ce    = "-|---____-----|--------___|-_-------",
                out   = "0|011111100110|10000001111|110000000",
                empty = "-|___________-|_______----|________-",
                get   = "_|-___________|-__________|--_______",
            ), "mutlistall shift out 3")

if __name__ == "__main__":
    unittest.main()
