#!/usr/bin/env python3

import unittest

from migen import *
from migen.fhdl.decorators import ResetInserter

from ..test.common import BaseUsbTestCase

from .bitstuff import RxBitstuffRemover

class TestRxBitstuffRemover(BaseUsbTestCase):
    def test_bitstuff(self):
        def set_input(dut, r, v):
            yield dut.reset.eq(r == '1')
            yield dut.i_data.eq(v == '1')

        def get_output(dut):
            # Read outputs
            o_data = yield dut.o_data
            o_stall = yield dut.o_stall
            o_error = yield dut.o_error

            if o_error:
                return 'e'
            elif o_stall:
                return's'
            else:
                return str(o_data)

        def send(reset, value):
            assert len(reset) == len(value), (reset, value)
            output = ""
            for i in range(len(value)+1):
                if i < len(value):
                    yield from set_input(dut, reset[i], value[i])

                yield

                if i > 0:
                    if reset[i-1] == '1':
                        output += '_'
                    else:
                        output += yield from get_output(dut)
            return output

        test_vectors = [
            # No bit stuff
            dict(
                reset  = "00000000000000000000",
                value  = "10110111011110111110",
                output = "10110111011110111110",
            ),

            # Bit stuff
            dict(
                reset  = "0000000",
                value  = "1111110",
                output = "111111s",
            ),

            # Bit stuff after reset
            dict(
                reset  = "00010000000",
                value  = "11111111110",
                output = "111_111111s",
            ),

            # Bit stuff error
            dict(
                reset  = "0000000",
                value  = "1111111",
                output = "111111e",
            ),

            # Bit stuff error after reset
            dict(
                reset  = "00010000000",
                value  = "11111111111",
                output = "111_111111e",
            ),

            dict(
                # Multiple bitstuff scenario
                reset  = "000000000000000000000",
                value  = "111111011111101111110",
                output = "111111s111111s111111s",
            ),

            dict(
                # Mixed bitstuff error
                reset  = "000000000000000000000000000000000",
                value  = "111111111111101111110111111111111",
                output = "111111e111111s111111s111111e11111",
            ),

            dict(
                # Idle, Packet, Idle
                reset  = "0000000000000000000000001100000",
                value  = "111110000000111111011101__11111",
                output = "111110000000111111s11101__11111",
            ),

            dict(
                # Idle, Packet, Idle, Packet, Idle
                reset  = "00000000000000000000000011000000000000000000000000000001100000",
                value  = "111110000000111111011101__11111111110000000111111011101__11111",
                output = "111110000000111111s11101__111111e1110000000111111s11101__11111",
            ),

            dict(
                # Captured setup packet (no bitstuff)
                reset  = "000000000000000000000000000000000110",
                value  = "100000001101101000000000000001000__1",
                output = "100000001101101000000000000001000__1"
            )
        ]

        def stim(output, **kw):
            actual_output = yield from send(**kw)
            self.assertEqual(output, actual_output)

        i = 0
        for vector in test_vectors:
            with self.subTest(i=i, vector=vector):
                dut = RxBitstuffRemover()

                run_simulation(
                    dut,
                    stim(**vector),
                    vcd_name=self.make_vcd_name(testsuffix="%02d" % i),
                )
                i += 1


if __name__ == "__main__":
    unittest.main()
