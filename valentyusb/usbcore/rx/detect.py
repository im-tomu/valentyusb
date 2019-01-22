#!/usr/bin/env python3

from migen import *
from migen.genlib import cdc

from migen.fhdl.decorators import ResetInserter

from ..test.common import BaseUsbTestCase
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
