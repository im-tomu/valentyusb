#!/usr/bin/env python3

import unittest

from migen import *

from ..test.common import BaseUsbTestCase
from .nrzi import RxNRZIDecoder


class TestRxNRZIDecoder(BaseUsbTestCase):
    def test_nrzi(self):

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

        test_vectors = [
            dict(
                # USB2 Spec, 7.1.8
                valid  = "-----------------",
                value  = "jkkkjjkkjkjjkjjjk",
                output = "10110101000100110"
            ),

            dict(
                # USB2 Spec, 7.1.9.1
                valid  = "--------------------",
                value  = "jkjkjkjkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ),

            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls)
                valid  = "------___--------------",
                value  = "jkjkjkkkkjkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ),

            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls 2)
                valid  = "-------___-------------",
                value  = "jkjkjkjjjjkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ),

            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls 3)
                valid  = "-------___-------------",
                value  = "jkjkjkjkkkkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ),

            dict(
                # USB2 Spec, 7.1.9.1 (added pipeline stalls, se0 glitch)
                valid  = "-------___-------------",
                value  = "jkjkjkj__kkkkkkkkjjjjkk",
                output = "10000000111111011101"
            ),

            dict(
                # Captured setup packet
                valid  = "------------------------------------",
                value  = "jkjkjkjkkkjjjkkjkjkjkjkjkjkjkkjkj__j",
                output = "100000001101101000000000000001000__1"
            ),

            dict(
                # Captured setup packet (pipeline stalls)
                valid  = "-___----___--------___-___-___-___----------------___-___---",
                value  = "jjjjkjkjjkkkjkkkjjjjjkkkkkkkkkjjjjkjkjkjkjkjkjkkjkkkkj_____j",
                output = "100000001101101000000000000001000__1"
            )

        ]

        def stim(valid, value, output):
            actual_output = yield from send(valid, value)
            self.assertEqual(actual_output, output)

        i = 0
        for vector in test_vectors:
            with self.subTest(i=i, vector=vector):
                dut = RxNRZIDecoder()

                run_simulation(
                    dut,
                    stim(**vector),
                    vcd_name=self.make_vcd_name(testsuffix="%02d" % i),
                )
                i += 1


if __name__ == "__main__":
    unittest.main()
