#!/usr/bin/env python3

import unittest

from migen import *
from migen.fhdl.decorators import ResetInserter

from ..test.common import BaseUsbTestCase
from .crc import RxCrcChecker

class TestRxCrcChecker(BaseUsbTestCase):
    def test_shifter(self):
        def send(reset, valid, value):
            crc_good = ""
            for i in range(len(valid)):
                yield i_reset.eq(reset[i] == '-')
                yield i_valid.eq(valid[i] == '-')
                yield i_data.eq(value[i] == '1')
                yield

                o_crc_good = yield dut.o_crc_good

                out = "%d" % (o_crc_good)

                crc_good += {
                    "1" : "-",
                    "0" : "_",
                }[out]

            return crc_good

        test_vectors = [
            dict(
                # USB2 token with good CRC5 (1)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-___________________",
                valid       = "_----------------___",
                value       = "00000000000001000000",
                crc_good    = "_______-__________--"
            ),

            dict(
                # USB2 token with good CRC5 and pipeline stalls (1)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-_______________________________",
                valid       = "_-___-___------------___-___-___",
                value       = "00000011100000000001011100000000",
                crc_good    = "_____________-________________--"
            ),

            dict(
            # USB2 token with bad CRC5 (1)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-___________________",
                valid       = "_----------------___",
                value       = "00010000000001000000",
                crc_good    = "______-________-____"
            ),

            dict(
                # USB2 token with good CRC5 (2)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-___________________",
                valid       = "_----------------___",
                value       = "00000011011011101000",
                crc_good    = "_______-__________--"
            ),

            dict(
                # USB2 token with bad CRC5 (2)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-___________________",
                valid       = "_----------------___",
                value       = "00010011011011101000",
                crc_good    = "______-_____________"
            ),

            dict(
                # Two USB2 token with good CRC5 (1,2)
                width       = 5,
                polynomial  = 0b00101,
                initial     = 0b11111,
                residual    = 0b01100,
                reset       = "-________________________-___________________",
                valid       = "_----------------_________----------------___",
                value       = "000000000000010000000000000000011011011101000",
                crc_good    = "_______-__________---------_____-__________--"
            ),

            dict(
                # USB2 data with good CRC16 (1)
                width       = 16,
                polynomial  = 0b1000000000000101,
                initial     = 0b1111111111111111,
                residual    = 0b1000000000001101,
                reset       = "-______________________________________________________________________________________________",
                valid       = "_--------_--------_--------_--------_--------_--------_--------_--------_----------------______",
                value       = "00000000100110000000000000001000000000000000000000000000000001000000000001011101100101001000010",
                crc_good    = "__________________________________________________________________________________________-----"
            ),

            dict(
                # USB2 data with bad CRC16 (1)
                width       = 16,
                polynomial  = 0b1000000000000101,
                initial     = 0b1111111111111111,
                residual    = 0b1000000000001101,
                reset       = "-______________________________________________________________________________________________",
                valid       = "_--------_--------_--------_--------_--------_--------_--------_--------_----------------______",
                value       = "00000000100110000000000000001000000000010000000000000000000001000000000001011101100101001000010",
                crc_good    = "_______________________________________________________________________________________________"
            ),
        ]

        def stim(width, polynomial, initial, residual, reset, valid, value, crc_good):
            actual_crc_good = yield from send(reset, valid, value)
            self.assertEqual(actual_crc_good, crc_good)

        for i, vector in enumerate(test_vectors):
            with self.subTest(i=i, vector=vector):
                i_valid = Signal()
                i_data = Signal()
                i_reset = Signal()

                dut = RxCrcChecker(
                    vector["width"],
                    vector["polynomial"],
                    vector["initial"],
                    vector["residual"],
                    i_valid,
                    i_data,
                    i_reset)

                run_simulation(
                    dut,
                    stim(**vector),
                    vcd_name=self.make_vcd_name(testsuffix=str(i)),
                )


if __name__ == "__main__":
    unittest.main()
