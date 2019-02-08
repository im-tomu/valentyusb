#!/usr/bin/env python3

import unittest

from migen import *

from ..test.common import BaseUsbTestCase
from .nrzi import RxNRZIDecoder


class TestRxNRZIDecoder(BaseUsbTestCase):
    def nrzi_test(self, vector, short_name):

        def send(valid, value):
            valid += "_"
            value += "_"
            output = ""
            for i in range(len(valid)):
                yield dut.i_valid.eq(valid[i] == '-')
                yield dut.i_dj.eq(value[i] == 'j')
                yield dut.i_dk.eq(value[i] == 'k')
                yield dut.i_se0.eq(value[i] == '_')
                yield

                o_valid = yield dut.o_valid
                if o_valid:
                    data = yield dut.o_data
                    se0 = yield dut.o_se0

                    out = "%d%d" % (data, se0)

                    output += {
                        "10" : "1",
                        "00" : "0",
                        "01" : "_",
                        "11" : "_"
                    }[out]
            return output


        def stim(valid, value, output):
            actual_output = yield from send(valid, value)
            self.assertEqual(actual_output, output)

        with self.subTest(short_name=short_name, vector=vector):
            dut = RxNRZIDecoder()

            run_simulation(
                dut,
                stim(**vector),
                vcd_name=self.make_vcd_name(testsuffix=short_name),
            )

    def test_usb2_spec_7_1_8(self):
        return self.nrzi_test(
            dict(
                # USB2 Spec, 7.1.8
                valid  = "-----------------",
                value  = "jkkkjjkkjkjjkjjjk",
                output = "10110101000100110"
            ), "usb2-spec-7.1.8")

    def test_usb2_spec_7_1_9_1(self):
        return self.nrzi_test(
            dict(
                # USB2 Spec, 7.1.9.1
                valid  = "--------------------",
                value  = "jkjkjkjkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ), "usb2-spec-7.1.9.1")

    def test_usb2_spec_7_1_9_1_stalls(self):
        return self.nrzi_test(
            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls)
                valid  = "------___--------------",
                value  = "jkjkjkkkkjkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ), "usb2-spec-7.1.9.1-stalls")

    def test_usb2_spec_7_1_9_1_stalls_2(self):
        return self.nrzi_test(
            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls 2)
                valid  = "-------___-------------",
                value  = "jkjkjkjjjjkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ), "usb2-spec-7.1.9.1-stalls-2")

    def test_usb2_spec_7_1_9_1_stalls_3(self):
        return self.nrzi_test(
            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls 3)
                valid  = "-------___-------------",
                value  = "jkjkjkjkkkkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ), "usb2-spec-7.1.9.1-stalls-3")

    def test_usb2_spec_7_1_9_1_stalls_se0_glitch(self):
        return self.nrzi_test(
            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls, se0 glitch)
                valid  = "-------___-------------",
                value  = "jkjkjkj__kkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ), "usb2-spec-7.1.9.1-stalls-se0-glitch")

    def test_captured_setup_packet(self):
        return self.nrzi_test(
            dict(
                # Captured setup packet
                valid  = "------------------------------------",
                value  = "jkjkjkjkkkjjjkkjkjkjkjkjkjkjkkjkj__j",
                output = "100000001101101000000000000001000__1"
            ), "captured-setup-packet")

    def test_captured_setup_packet_stalls(self):
        return self.nrzi_test(
            dict(
                # Captured setup packet (pipeline stalls)
                valid  = "-___----___--------___-___-___-___----------------___-___---",
                value  = "jjjjkjkjjkkkjkkkjjjjjkkkkkkkkkjjjjkjkjkjkjkjkjkkjkkkkj_____j",
                output = "100000001101101000000000000001000__1"
            ), "test-captured-setup-packet-stalls")


if __name__ == "__main__":
    unittest.main()
