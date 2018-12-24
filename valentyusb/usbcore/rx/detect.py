#!/usr/bin/env python3

from migen import *
from migen.genlib import cdc

from migen.fhdl.decorators import ResetInserter

import unittest


@ResetInserter()
class RxPacketDetect(Module):
    """Packet Detection

    Full Speed packets begin with the following sequence:

        KJKJKJKK

    This raw sequence corresponds to the following data:

        00000001

    The bus idle condition is signaled with the J state:

        JJJJJJJJ

    This translates to a series of '1's since there are no transitions.  Given
    this information, it is easy to detect the beginning of a packet by looking
    for 00000001.

    The end of a packet is even easier to detect.  The end of a packet is
    signaled with two SE0 and one J.  We can just look for the first SE0 to
    detect the end of the packet.

    Packet detection can occur in parallel with bitstuff removal.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.10

    Input Ports
    ------------
    i_data : Signal(1)
        Decoded data bit from USB bus.

    Output Ports
    ------------
    o_pkt_start : Signal(1)
        Asserted for one clock on the last bit of the sync.

    o_pkt_active : Signal(1)
        Asserted while in the middle of a packet.
    """

    def __init__(self):
        self.i_data = Signal()

        self.submodules.pkt = pkt = FSM()

        pkt_start = Signal()
        pkt_active = Signal()

        for i in range(5):
            pkt.act("D%d" % i,
                If(self.i_data,
                    # Receiving '1' or SE0 early resets the packet start counter.
                    NextState("D0")
                ).Else(
                    # Receiving '0' increments the packet start counter.
                    NextState("D%d" % (i + 1))
                )
            )

        pkt.act("D5",
            # once we get a '1', the packet is active
            If(self.i_data,
                pkt_start.eq(1),
                NextState("PKT_ACTIVE")
            )
        )

        pkt.act("PKT_ACTIVE",
            pkt_active.eq(1),
        )

        # pass all of the outputs through a pipe stage
        self.o_pkt_start = Signal(1)
        self.o_pkt_active = Signal(1)
        self.comb += [
            self.o_pkt_start.eq(pkt_start),
            self.o_pkt_active.eq(pkt_active),
        ]


class TestRxPacketDetect(unittest.TestCase):
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

                run_simulation(dut, stim(**vector), vcd_name="vcd/test_packet_det_%d.vcd" % i)
                i += 1


if __name__ == "__main__":
    unittest.main()
