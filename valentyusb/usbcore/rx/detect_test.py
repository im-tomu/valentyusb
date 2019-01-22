#!/usr/bin/env python3

import unittest

from migen import *
from migen.genlib import cdc

from migen.fhdl.decorators import ResetInserter

from ..test.common import BaseUsbTestCase
from .detect import RxPacketDetect


class TestRxPacketDetect(BaseUsbTestCase):
    def test_packet_detect(self):

        test_vectors = [
            dict(
                # SE0, Idle
                value      = "______________111111111111111",
                pkt_start  = "                              ",
                pkt_active = "______________________________"
            ),

            dict(
                # Idle, Packet, Idle
                value      = "11111000000011111111101__11111",
                pkt_start  = "             S                 ",
                pkt_active = "_____________-----------_______"
            ),

            dict(
                # Idle, Packet, Idle (pipeline stall)
                value      = "111110000000111111111101__11111",
                pkt_start  = "             S                  ",
                pkt_active = "_____________------------_______"
            ),

            dict(
                # Idle, Packet, Idle (pipeline stalls)
                value      = "11111000000011111111111101__11111",
                pkt_start  = "             S                    ",
                pkt_active = "_____________--------------_______"
            ),

            dict(
                # Idle, Packet, Idle, Packet, Idle
                value      = "11111000000011111111101__1111111111000000011111111101__11111",
                pkt_start  = "             S                             S                 ",
                pkt_active = "_____________-----------___________________-----------_______"
            ),

            dict(
                # Idle, Short Sync Packet, Idle
                value      = "111110000011111111101__11111",
                pkt_start  = "           S                 ",
                pkt_active = "___________-----------_______"
            ),

            dict(
                # Idle Glitch
                value      = "11111111110011111111_1111__111",
                pkt_start  = "                               ",
                pkt_active = "_______________________________"
            ),
        ]

        def send(value):
            value += "_"
            pkt_start = ""
            pkt_active = ""
            for i in range(len(value)):
                if i < len(value):
                    yield dut.i_data.eq(value[i] == '1')
                    yield dut.reset.eq(value[i] == '_')

                yield

                pkt_start += {
                    1 : "S",
                    0 : " ",
                }[(yield dut.o_pkt_start)]

                pkt_active += {
                    1 : "-",
                    0 : "_",
                }[(yield dut.o_pkt_active)]

            return pkt_start, pkt_active

        def stim(value, pkt_start, pkt_active):
            actual_pkt_start, actual_pkt_active = yield from send(value)
            self.assertSequenceEqual(pkt_start, actual_pkt_start)
            self.assertSequenceEqual(pkt_active, actual_pkt_active)

        i = 0
        for vector in test_vectors:
            with self.subTest(i=i, vector=vector):
                dut = RxPacketDetect()

                run_simulation(
                    dut,
                    stim(**vector),
                    vcd_name=self.make_vcd_name(testsuffix=str(i)),
                )
                i += 1


if __name__ == "__main__":
    unittest.main()
