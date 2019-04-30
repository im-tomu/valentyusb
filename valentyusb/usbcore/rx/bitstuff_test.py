#!/usr/bin/env python3

import unittest

from migen import *
from migen.fhdl.decorators import ResetInserter

from ..test.common import BaseUsbTestCase

from .bitstuff import RxBitstuffRemover

class TestRxBitstuffRemover(BaseUsbTestCase):
    def bitstuff_test(self, vector, short_name):
        def set_input(dut, r, d, v):
            yield dut.reset.eq(r == '1')
            yield dut.i_data.eq(d== '1')
            yield dut.i_valid.eq(v == '-')

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

        def send(reset, value, valid):
            assert len(reset) == len(value) == len(valid), (reset, value, valid)
            output = ""
            for i in range(len(value)+1):
                if i < len(value):
                    yield from set_input(dut, reset[i], value[i], valid[i])

                yield

                if i > 0:
                    if reset[i-1] == '1':
                        output += '_'
                    else:
                        output += yield from get_output(dut)
            return output

        def stim(output, **kw):
            actual_output = yield from send(**kw)
            self.assertEqual(output, actual_output)

        with self.subTest(short_name=short_name, vector=vector):
            dut = RxBitstuffRemover()

            run_simulation(
                dut,
                stim(**vector),
                vcd_name=self.make_vcd_name(testsuffix=short_name),
            )

    def test_no_bit_stuff(self):
        return self.bitstuff_test(
            # No bit stuff
            dict(
                reset  = "00000000000000000000",
                value  = "10110111011110111110",
                valid  = "--------------------",
                output = "10110111011110111110",
            ), "no-bit-stuff")

    def test_bit_stuff(self):
        return self.bitstuff_test(
            # Bit stuff
            dict(
                reset  = "0000000",
                value  = "1111110",
                valid  = "-------",
                output = "111111s",
            ), "bit-stuff")

    def test_bit_stuff_after_reset(self):
        return self.bitstuff_test(
            # Bit stuff after reset
            dict(
                reset  = "00010000000",
                value  = "11111111110",
                valid  = "-----------",
                output = "111_111111s",
            ), "bit-stuff-after-reset")

    def test_bit_stuff_error(self):
        return self.bitstuff_test(
            # Bit stuff error
            dict(
                reset  = "0000000",
                value  = "1111111",
                valid  = "-------",
                output = "111111e",
            ), "bit_stuff_error")

    def test_bit_stuff_error_after_reset(self):
        return self.bitstuff_test(
            # Bit stuff error after reset
            dict(
                reset  = "00010000000",
                value  = "11111111111",
                valid  = "-----------",
                output = "111_111111e",
            ), "bit-stuff-error-after-reset")

    def test_multiple_bit_stuff_scenario(self):
        return self.bitstuff_test(
            dict(
                # Multiple bitstuff scenario
                reset  = "000000000000000000000",
                value  = "111111011111101111110",
                valid  = "---------------------",
                output = "111111s111111s111111s",
            ), "multiple-bit-stuff-scenario")

    def test_mixed_bit_stuff_error(self):
        return self.bitstuff_test(
            dict(
                # Mixed bitstuff error
                reset  = "000000000000000000000000000000000",
                value  = "111111111111101111110111111111111",
                valid  = "---------------------------------",
                output = "111111e111111s111111s111111e11111",
            ), "mixed-bit-stuff-error")

    def test_idle_packet_idle(self):
        return self.bitstuff_test(
            dict(
                # Idle, Packet, Idle
                reset  = "0000000000000000000000001100000",
                value  = "111110000000111111011101__11111",
                valid  = "-------------------------------",
                output = "111110000000111111s11101__11111",
            ), "idle-packet-idle")

    def test_idle_packet_idle_packet_idle(self):
        return self.bitstuff_test(
            dict(
                # Idle, Packet, Idle, Packet, Idle
                reset  = "00000000000000000000000011000000000000000000000000000001100000",
                value  = "111110000000111111011101__11111111110000000111111011101__11111",
                valid  = "--------------------------------------------------------------",
                output = "111110000000111111s11101__111111e1110000000111111s11101__11111",
            ), "idle-packet-idle-packet-idle")

    def test_captured_setup_packet_no_stuff(self):
        return self.bitstuff_test(
            dict(
                # Captured setup packet (no bitstuff)
                reset  = "000000000000000000000000000000000110",
                value  = "100000001101101000000000000001000__1",
                valid  = "------------------------------------",
                output = "100000001101101000000000000001000__1"
            ), "captured-setup-packet-no-stuff")

    def test_valid_idle_packet_idle_packet_idle(self):
        return self.bitstuff_test(
            dict(
                # Idle, Packet, Idle, Packet, Idle
                reset  = "00000000000000000000000000110000000000000000000000000000000001100000",
                value  = "11111000000001111111011101__111101111110000100011110111011101__11111",
                valid  = "--------_-------_---------------_----------_------__---------_------",
                output = "11111000s0000111s111s11101__1111s11e1110000s000111ss111s11101__11111",
            ), "valid-idle-packet-idle-packet-idle")

    def test_valid_captured_setup_packet_no_stuff(self):
        return self.bitstuff_test(
            dict(
                # Captured setup packet (no bitstuff)
                reset  = "000000000000000000000000000000000000000110",
                value  = "100000000110111010000000000000000001000__1",
                valid  = "-----_--------_-----_----___--------------",
                output = "10000s00011011s01000s0000sss00000001000__1"
            ), "valid-captured-setup-packet-no-stuff")


if __name__ == "__main__":
    unittest.main()
