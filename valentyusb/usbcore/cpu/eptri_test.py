#!/usr/bin/env python3

import unittest
from unittest import TestCase

from migen import *

from ..endpoint import EndpointType, EndpointResponse
from ..io_test import FakeIoBuf
from ..pid import PIDTypes
from ..utils.packet import crc16

from ..test.common import BaseUsbTestCase, CommonUsbTestCase
from ..test.clock import CommonTestMultiClockDomain

from .eptri import TriEndpointInterface


class TestTriEndpointInterface(
        BaseUsbTestCase,
        CommonUsbTestCase,
        CommonTestMultiClockDomain,
        unittest.TestCase):

    maxDiff=None

    # def get_endpoint(self, epaddr):
    #     epdir = EndpointType.epdir(epaddr)
    #     epnum = EndpointType.epnum(epaddr)
    #     if epdir == EndpointType.OUT:
    #         return getattr(self.dut, "ep_%s_out" % epnum)
    #     elif epdir == EndpointType.IN:
    #         return getattr(self.dut, "ep_%s_in" % epnum)
    #     else:
    #         raise SystemError("Unknown endpoint type: %r" % epdir)

    def on_usb_48_edge(self):
        if False:
            yield

    def on_usb_12_edge(self):
        if False:
            yield

    def setUp(self):
        CommonTestMultiClockDomain.setUp(self, ("usb_12", "usb_48"))

        # Only enable "debug" mode for tests with "debug" in their name
        if "debug" in self._testMethodName:
            debug = True
        else:
            debug = False

        self.iobuf = FakeIoBuf()
        self.dut = TriEndpointInterface(self.iobuf, debug=debug)

        self.packet_h2d = Signal(1)
        self.packet_d2h = Signal(1)
        self.packet_idle = Signal(1)

    def run_sim(self, stim):
        def padfront():
            yield
            yield
            yield
            yield
            yield
            yield
            # # Make sure that the endpoints are currently blocked
            # opending = yield self.dut.epout.ev.packet.pending
            # self.assertTrue(opending)
            # ipending = yield self.dut.epin.ev.packet.pending
            # self.assertTrue(ipending)
            # yield
            # yield from self.dut.pullup._out.write(1)
            # yield
            # # Make sure that the endpoints are currently blocked but not being
            # # triggered.
            # opending = yield self.dut.epout.ev.packet.pending
            # self.assertTrue(opending)
            # ipending = yield self.dut.epin.ev.packet.pending
            # self.assertTrue(ipending)

            # otrigger = yield self.dut.epout.ev.packet.trigger
            # self.assertFalse(otrigger)
            # itrigger = yield self.dut.epin.ev.packet.trigger
            # self.assertFalse(itrigger)

            yield
            yield from self.idle()
            yield from stim()

        run_simulation(
            self.dut,
            padfront(),
            vcd_name=self.make_vcd_name(),
            #clocks={
            #    "sys": 12,
            #    "usb_48": 48,
            #    "usb_12": 192,
            #},
            clocks={
                "sys": 2,
                "usb_48": 8,
                "usb_12": 32,
            },
        )
        print("-"*10)

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

    # IRQ / packet pending -----------------
    def trigger(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        status = yield endpoint.ev.packet.trigger
        return bool(status)

    def pending(self, epaddr):
        # epdir = EndpointType.epdir(epaddr)
        # epnum = EndpointType.epnum(epaddr)
        # if epdir == EndpointType.OUT:
        #     status = yield from self.dut.epout.status.read()
        # else:
        #     status = yield from self.dut.epin.status.read()
        # return (status & 1)
        return 1

    def clear_pending(self, epaddr):
        # Can't clear pending while trigger is active.
        for i in range(0, 100):
            trigger = (yield from self.trigger(epaddr))
            if not trigger:
                break
            yield from self.tick_sys()
        self.assertFalse(trigger)
        # Check the pending flag is raised
        self.assertTrue((yield from self.pending(epaddr)))
        # Clear pending flag
        endpoint = self.get_endpoint(epaddr)
        yield from endpoint.ev.pending.write(0xf)
        yield from self.tick_sys()
        # Check the pending flag has been cleared
        self.assertFalse((yield from self.trigger(epaddr)))
        self.assertFalse((yield from self.pending(epaddr)))

    # Endpoint state -----------------------
    def response(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        response = yield endpoint.response
        return response

    def set_response(self, epaddr, v):
        endpoint = self.get_endpoint(epaddr)
        assert isinstance(v, EndpointResponse), v
        yield from endpoint.respond.write(v)

    def expect_last_tok(self, epaddr, value):
        endpoint = self.get_endpoint(epaddr)
        last_tok = yield from endpoint.last_tok.read()
        self.assertEqual(last_tok, value)

    # Get/set endpoint data ----------------
    def set_data(self, epaddr, data):
        """Set an endpoints buffer to given data to be sent."""
        assert isinstance(data, (list, tuple))
        self.ep_print(epaddr, "Set: %r", data)

        endpoint = self.get_endpoint(epaddr)

        # Make sure the endpoint is empty
        empty = yield from endpoint.ibuf_empty.read()
        self.assertTrue(
            empty, "Device->Host buffer not empty when setting data!")

        # If we are writing multiple bytes of data, need to make sure we are
        # not going to ACK the packet until the data is ready.
        if len(data) > 1:
            response = yield endpoint.response
            self.assertNotEqual(response, EndpointResponse.ACK)

        for v in data:
            yield from endpoint.ibuf_head.write(v)
            yield from self.tick_sys()

        for i in range(0, 10):
            yield from self.tick_usb12()

        empty = yield from endpoint.ibuf_empty.read()
        if len(data) > 0:
            self.assertFalse(
                bool(empty), "Buffer not empty after setting zero data!")
        else:
            self.assertTrue(
                bool(empty), "Buffer empty after setting data!")

    def expect_setup(self, epaddr, data):
        epnum = EndpointType.epnum(epaddr)
        status = yield from self.dut.setup.status.read()
        self.assertTrue(status & 1)
        self.assertEqual(epnum, (status >> 1) & 15)

        actual_data = []
        for i in range(0, 16):
            print("Loop {}".format(i))
            yield from self.dut.setup.ctrl.write(1)
            have = (yield from self.dut.setup.status.read()) & 1
            if not have:
                break
            v = yield from self.dut.setup.data.read()
            actual_data.append(v)
            yield

        assert len(actual_data) >= 2, actual_data
        actual_data, actual_crc16 = actual_data[:-2], actual_data[-2:]

        self.ep_print(epaddr, "Got: %r (expected: %r)", actual_data, data)
        self.assertSequenceEqual(data, actual_data)
        self.assertSequenceEqual(crc16(data), actual_crc16)

    def expect_data(self, epaddr, data):
        """Expect that an endpoints buffer has given contents."""
        epdir = EndpointType.epdir(epaddr)
        epnum = EndpointType.epnum(epaddr)

        self.assertTrue(epdir == EndpointType.OUT)

        # Make sure there is something pending
        self.assertTrue((yield from self.dut.epout.status.read()) & 1)

        actual_data = []
        while range(0, 1024):
            yield from self.dut.epout.ctrl.write(1)
            empty = yield from self.dut.epout.status.read() & 1
            if empty:
                break

            v = yield from self.dut.epout.data.read()
            actual_data.append(v)
            yield

        assert len(actual_data) >= 2, actual_data
        actual_data, actual_crc16 = actual_data[:-2], actual_data[-2:]

        self.ep_print(epaddr, "Got: %r (expected: %r)", actual_data, data)
        self.assertSequenceEqual(data, actual_data)
        self.assertSequenceEqual(crc16(data), actual_crc16)

    def dtb(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        status = yield from endpoint.dtb.read()
        return bool(status)


if __name__ == '__main__':
    unittest.main()
