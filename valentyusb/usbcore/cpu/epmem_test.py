#!/usr/bin/env python3

import unittest
from unittest import TestCase

from migen import *

from ..test.common import BaseUsbTestCase, CommonUsbTestCase
from ..io_test import FakeIoBuf

from .epmem import MemInterface
from ..endpoint import EndpointType, EndpointResponse
from ..test.clock import CommonTestMultiClockDomain
from ..utils.bits import get_bit, set_bit

class TestMemInterface(
        BaseUsbTestCase,
        CommonUsbTestCase,
        CommonTestMultiClockDomain,
        unittest.TestCase):

    maxDiff=None

    def on_usb_48_edge(self):
        if False:
            yield

    def on_usb_12_edge(self):
        if False:
            yield

    def setUp(self):
        CommonTestMultiClockDomain.setUp(self, ("usb_12", "usb_48"))
        self.iobuf = FakeIoBuf()
        self.dut = MemInterface(self.iobuf, num_endpoints=3)

        self.packet_h2d = Signal(1)
        self.packet_d2h = Signal(1)
        self.packet_idle = Signal(1)

    def run_sim(self, stim):
        def padfront():
            yield
            yield
            yield
            yield
            yield from self.dut.pullup._out.write(1)
            yield
            yield
            yield
            yield
            yield from self.idle()
            yield from stim()

        # print()
        # print("-"*10)
        CommonUsbTestCase.patch_csrs(self)
        run_simulation(
            self.dut,
            padfront(),
            vcd_name=self.make_vcd_name(),
            clocks={
                "sys": 2,
                "usb_48": 8,
                "usb_12": 32,
            },
        )
        # print("-"*10)

    def tick_sys(self):
        yield from self.update_internal_signals()
        yield

    def tick_usb48(self):
        yield from self.wait_for_edge("usb_48")

    def tick_usb12(self):
        for i in range(0, 4):
            yield from self.tick_usb48()

    def update_internal_signals(self):
        yield from self.update_clocks()

    ######################################################################
    ## Helpers
    ######################################################################

    def get_module(self, epaddr, name, obj=None):
        if obj is None:
            obj = self.dut
        epdir = EndpointType.epdir(epaddr)
        if epdir == EndpointType.OUT:
            module = getattr(obj, 'o{}'.format(name))
        elif epdir == EndpointType.IN:
            module = getattr(obj, 'i{}'.format(name))
        else:
            raise SystemError("Unknown endpoint type: %r" % epdir)
        return module

    def get_evsrc(self, epaddr):
        epnum = EndpointType.epnum(epaddr)
        epdir = EndpointType.epdir(epaddr)
        # if epdir == EndpointType.OUT:
            # print("get_evsrc name: oep{}".format(epnum))
        # elif epdir == EndpointType.IN:
            # print("get_evsrc name: iep{}".format(epnum))
        return self.get_module(epaddr, "ep{}".format(epnum), obj=self.dut.ev)

    def get_ptr_csr(self, epaddr):
        epnum = EndpointType.epnum(epaddr)
        return self.get_module(epaddr, "ptr_ep{}".format(epnum))

    def get_len_csr(self, epaddr):
        epnum = EndpointType.epnum(epaddr)
        return self.get_module(epaddr, "len_ep{}".format(epnum))

    def set_csr(self, csr, epaddr, v):
        c = yield from csr.read()
        v = set_bit(c, epaddr, v)
        yield from csr.write(v)

    # Data Toggle Bit
    def dtb(self, epaddr):
        v = yield from self.dut.dtb.read()
        return get_bit(epaddr, v)

    def set_dtb(self, epaddr):
        yield from self.set_csr(self.dut.dtb, epaddr, 1)

    def clear_dtb(self, epaddr):
        yield from self.set_csr(self.dut.dtb, epaddr, 0)

    # Arm endpoint Bit
    def arm(self, epaddr):
        v = yield from self.dut.arm.read()
        return get_bit(epaddr, v)

    def set_arm(self, epaddr):
        yield from self.set_csr(self.dut.arm, epaddr, 1)

    def clear_arm(self, epaddr):
        yield from self.set_csr(self.dut.arm, epaddr, 0)

    # Stall endpoint Bit
    def sta(self, epaddr):
        v = yield from self.dut.sta.read()
        return get_bit(epaddr, v)

    def set_sta(self, epaddr):
        yield from self.set_csr(self.dut.sta, epaddr, 1)

    def clear_sta(self, epaddr):
        yield from self.set_csr(self.dut.sta, epaddr, 0)

    # IRQ / packet pending -----------------
    def trigger(self, epaddr):
        evsrc = self.get_evsrc(epaddr)
        v = yield evsrc.trigger
        return v

    def pending(self, epaddr):
        # print("epaddr: {}".format(epaddr))
        evsrc = self.get_evsrc(epaddr)
        v = yield evsrc.pending
        return v

    def clear_pending(self, epaddr):
        # Can't clear pending while trigger is active.
        while True:
            trigger = (yield from self.trigger(epaddr))
            if not trigger:
                break
            yield

        # Check the pending flag is raised
        self.assertTrue((yield from self.pending(epaddr)))

        # Clear pending flag
        mask = 1 << epaddr
        yield from self.dut.ev.pending.write(mask)
        yield
        # Check the pending flag has been cleared
        self.assertFalse((yield from self.trigger(epaddr)))
        self.assertFalse((yield from self.pending(epaddr)))

    # Endpoint state -----------------------
    def response(self, epaddr):
        if (yield from self.sta(epaddr)):
            return EndpointResponse.STALL

        pending = yield from self.pending(epaddr)
        armed = yield from self.arm(epaddr)
        if armed and not pending:
            return EndpointResponse.ACK

        return EndpointResponse.NAK

    def format_epaddr(self, ep):
        epdir = "IN"
        if ep & 1 == 0:
            epdir = "OUT"
        return "{} - EP{} ({})".format(ep, ep >> 1, epdir)

    def set_response(self, epaddr, v):
        assert isinstance(v, EndpointResponse), v
        if v == EndpointResponse.STALL:
            # print("Setting response to {} to STALL".format(self.format_epaddr(epaddr)))
            yield from self.set_sta(epaddr)
            yield from self.clear_arm(epaddr)
        elif v == EndpointResponse.ACK:
            # print("Setting response to {} to ACK".format(self.format_epaddr(epaddr)))
            yield from self.clear_sta(epaddr)
            yield from self.set_arm(epaddr)
        elif v == EndpointResponse.NAK:
            # print("Setting response to {} to NAK".format(self.format_epaddr(epaddr)))
            yield from self.clear_sta(epaddr)
            yield from self.clear_arm(epaddr)
        else:
            print("Unknown EP response to {}: {}".format(self.format_epaddr(epaddr), v))

    # Get/set endpoint data ----------------
    def set_data(self, epaddr, data):
        """Set an endpoints buffer to given data to be sent."""
        assert isinstance(data, (list, tuple))
        # self.ep_print(epaddr, "Set: %r", data)

        ep_ptr = yield from self.get_ptr_csr(epaddr).read()
        buf = self.get_module(epaddr, "buf")

        # # Make sure the endpoint is empty
        # empty = yield from endpoint.ibuf_empty.read()
        # self.assertTrue(
        #     empty, "Device->Host buffer not empty when setting data!")

        for i, v in enumerate(data):
            yield buf[ep_ptr+i].eq(v)

        ep_len = self.get_len_csr(epaddr)
        yield from ep_len.write(ep_ptr + len(data))

        yield

    def expect_data(self, epaddr, data):
        """Expect that an endpoints buffer has given contents."""
        ep_ptr = yield from self.get_ptr_csr(epaddr).read()
        buf = self.get_module(epaddr, "buf")

        # actual_data = []
        # while range(0, 1024):
        #     yield from endpoint.obuf_head.write(0)
        #     empty = yield from endpoint.obuf_empty.read()
        #     if empty:
        #         break

        #     v = yield from endpoint.obuf_head.read()
        #     actual_data.append(v)
        #     yield

        # Make sure there is something pending
        self.assertTrue((yield from self.pending(epaddr)))

        actual_data = []
        for i in range(len(data), 0, -1):
            # Subtract two bytes, since the CRC16 is stripped from the buffer.
            d = yield buf[ep_ptr-i-2]
            actual_data.append(d)

        msg = "\n"

        loop=0
        msg = msg + "Wanted: ["
        for var in data:
            if loop > 0:
                msg = msg + ", "
            msg = msg + "0x{:02x}".format(var)
            loop = loop + 1
        msg = msg + "]\n"

        loop=0
        msg = msg + "   Got: ["
        for var in actual_data:
            if loop > 0:
                msg = msg + ", "
            msg = msg + "0x{:02x}".format(var)
            loop = loop + 1
        msg = msg + "]"
    
        # self.ep_print(epaddr, msg)
        self.assertSequenceEqual(data, actual_data, msg)


if __name__ == '__main__':
    unittest.main()
