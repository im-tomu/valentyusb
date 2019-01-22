#!/usr/bin/env python3

import unittest
from unittest import TestCase

from migen import *

from ..test.common import BaseUsbTestCase, CommonUsbTestCase
from ..io_test import TestIoBuf

from .epmem import MemInterface


class TestMemInterface(
        BaseUsbTestCase,
        CommonUsbTestCase,
        unittest.TestCase):

    maxDiff=None

    def setUp(self):
        self.iobuf = TestIoBuf()
        self.dut = ClockDomainsRenamer("cpu_12")(
            MemInterface(self.iobuf, num_endpoints=3))

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

        print()
        print("-"*10)
        run_simulation(
            self.dut,
            padfront(),
            vcd_name=self.make_vcd_name(),
            clocks={
                "sys": 12,
                "usb_48": 48,
                "usb_12": 192,
                "cpu_12": 192,
            },
        )
        print("-"*10)

    def _update_internal_signals(self):
        if False:
            yield

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
        return self.get_module(epaddr, "ep{}".format(epnum), obj=self.dut.packet)

    def get_ptr_csr(self, epaddr):
        epnum = EndpointType.epnum(epaddr)
        return self.get_module(epaddr, "ptr_ep{}".format(epnum))

    def get_len_csr(self, epaddr):
        epnum = EndpointType.epnum(epaddr)
        return self.get_module(epaddr, "len_ep{}".format(epnum))

    def set_csr(self, csr, epaddr, v):
        c = yield from csr.read()
        v = _set_bit(c, epaddr, v)
        yield from csr.write(v)

    # Data Toggle Bit
    def dtb(self, epaddr):
        v = yield from self.dut.dtb.read()
        return _get_bit(epaddr, v)

    def set_dtb(self, epaddr):
        yield from self.set_csr(self.dut.dtb, epaddr, 1)

    def clear_dtb(self, epaddr):
        yield from self.set_csr(self.dut.dtb, epaddr, 0)

    # Arm endpoint Bit
    def arm(self, epaddr):
        v = yield from self.dut.arm.read()
        return _get_bit(epaddr, v)

    def set_arm(self, epaddr):
        yield from self.set_csr(self.dut.arm, epaddr, 1)

    def clear_arm(self, epaddr):
        yield from self.set_csr(self.dut.arm, epaddr, 0)

    # Stall endpoint Bit
    def sta(self, epaddr):
        v = yield from self.dut.sta.read()
        return _get_bit(epaddr, v)

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
        yield from self.dut.packet.pending.write(mask)
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

    def set_response(self, epaddr, v):
        assert isinstance(v, EndpointResponse), v
        if v == EndpointResponse.STALL:
            yield from self.set_sta(epaddr)
        else:
            yield from self.clear_sta(epaddr)

        if v == EndpointResponse.ACK:
            yield from self.set_arm(epaddr)
        elif v == EndpointResponse.NAK:
            yield from self.clear_arm(epaddr)

    # Get/set endpoint data ----------------
    def set_data(self, epaddr, data):
        """Set an endpoints buffer to given data to be sent."""
        assert isinstance(data, (list, tuple))
        self.ep_print(epaddr, "Set: %r", data)

        ep_ptr = yield from self.get_ptr_csr(epaddr).read()
        buf = self.get_module(epaddr, "buf")

        for i, v in enumerate(data):
            yield buf[ep_ptr+i].eq(v)

        ep_len = self.get_len_csr(epaddr)
        yield from ep_len.write(ep_ptr + len(data))

        yield

    def expect_data(self, epaddr, data):
        """Expect that an endpoints buffer has given contents."""
        ep_ptr = yield from self.get_ptr_csr(epaddr).read()
        buf = self.get_module(epaddr, "buf")

        # Make sure there is something pending
        self.assertTrue((yield from self.pending(epaddr)))

        actual_data = []
        for i in range(len(data), 0, -1):
            d = yield buf[ep_ptr-i]
            actual_data.append(d)

        self.ep_print(epaddr, "Got: %r (expected: %r)", actual_data, data)
        self.assertSequenceEqual(data, actual_data)


if __name__ == '__main__':
    unittest.main()
