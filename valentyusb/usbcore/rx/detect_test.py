#!/usr/bin/env python3

import unittest

from migen import *
from migen.genlib import cdc

from migen.fhdl.decorators import ResetInserter

from ..test.common import BaseUsbTestCase
from .detect import RxPacketDetect


class TestRxPacketDetect(BaseUsbTestCase):
    def packet_detect_test(self, vector, short_name):
        def send(value, valid):
            value += "_"
            pkt_start = ""
            pkt_active = ""
            for i in range(len(value)):
                if i < len(value):
                    yield dut.i_data.eq(value[i] == '1')
                    yield dut.i_valid.eq(valid[i] == '-')
                    yield dut.reset.eq(value[i] == '_' and valid[i] == '-')

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

        def stim(value, valid, pkt_start, pkt_active):
            actual_pkt_start, actual_pkt_active = yield from send(value, valid)
            self.assertSequenceEqual(pkt_start, actual_pkt_start)
            self.assertSequenceEqual(pkt_active, actual_pkt_active)

        with self.subTest(short_name=short_name, vector=vector):
            dut = RxPacketDetect()

            run_simulation(
                dut,
                stim(**vector),
                vcd_name=self.make_vcd_name(testsuffix=short_name),
            )

    def test_se0_idle(self):
        return self.packet_detect_test(
            dict(
                # SE0, Idle
                value      = "______________111111111111111",
                valid      = "------------------------------",
                pkt_start  = "                              ",
                pkt_active = "______________________________"
            ), "se0-idle")

    def test_idle_packet_idle(self):
        return self.packet_detect_test(
            dict(
                # Idle, Packet, Idle
                value      = "11111000000011111111101__11111",
                valid      = "-------------------------------",
                pkt_start  = "            S                  ",
                pkt_active = "_____________-----------_______"
            ), "idle-packet-idle")

    def test_idle_packet_idle_stall(self):
        return self.packet_detect_test(
            dict(
                # Idle, Packet, Idle (pipeline stall)
                value      = "111110000000111111111101__11111",
                valid      = "--------------------------------",
                pkt_start  = "            S                   ",
                pkt_active = "_____________------------_______"
            ), "idle-packet-idle-stall")

    def test_idle_packet_idle_stalls(self):
        return self.packet_detect_test(
            dict(
                # Idle, Packet, Idle (pipeline stalls)
                value      = "11111000000011111111111101__11111",
                valid      = "----------------------------------",
                pkt_start  = "            S                     ",
                pkt_active = "_____________--------------_______"
            ), "idle-packet-idle-stalls")

    def test_idle_packet_idle_packet_idle(self):
        return self.packet_detect_test(
            dict(
                # Idle, Packet, Idle, Packet, Idle
                value      = "11111000000011111111101__1111111111000000011111111101__11111",
                valid      = "-------------------------------------------------------------",
                pkt_start  = "            S                             S                  ",
                pkt_active = "_____________-----------___________________-----------_______"
            ), "idle-packet-idle-packet-idle")

    def test_idle_sync_idle(self):
        return self.packet_detect_test(
            dict(
                # Idle, Short Sync Packet, Idle
                value      = "111110000011111111101__11111",
                valid      = "-----------------------------",
                pkt_start  = "          S                  ",
                pkt_active = "___________-----------_______"
            ), "idle-shortsyncpacket-idle")

    def test_idle_glitch(self):
        return self.packet_detect_test(
            dict(
                # Idle Glitch
                value      = "11111111110011111111_1111__111",
                valid      = "-------------------------------",
                pkt_start  = "                               ",
                pkt_active = "_______________________________"
            ), "idle-glitch")

    def test_valid_idle_packet_idle_packet_idle(self):
        return self.packet_detect_test(
            dict(
                # Idle, Packet, Idle, Packet, Idle
                value      = "11111100100000011111111110101___111111111110000000011111111101___11111",
                valid      = "----_---_--_---_------_--_----_-----_----------_--------------_--------",
                pkt_start  = "                S                                  S                   ",
                pkt_active = "_________________-------------______________________------------_______"
            ), "valid-idle-packet-idle-packet-idle")


if __name__ == "__main__":
    unittest.main()
