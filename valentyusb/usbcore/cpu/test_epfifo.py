#!/usr/bin/env python3

import unittest
from unittest import TestCase


class TestPerEndpointFifoInterface(CommonUsbTestCase):

    maxDiff=None

    def setUp(self):
        self.endpoints=[EndpointType.BIDIR, EndpointType.IN, EndpointType.BIDIR]

        self.iobuf = TestIoBuf()
        self.dut = UsbDeviceCpuInterface(self.iobuf, self.endpoints)

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
            # Make sure that the endpoints are currently blocked
            ostatus = yield self.dut.ep_0_out.ev.packet.pending
            self.assertTrue(ostatus)
            istatus = yield self.dut.ep_0_in.ev.packet.pending
            self.assertTrue(istatus)
            yield
            yield from self.dut.pullup._out.write(1)
            yield
            # Make sure that the endpoints are currently blocked
            ostatus = yield self.dut.ep_0_out.ev.packet.pending
            self.assertTrue(ostatus)
            istatus = yield self.dut.ep_0_in.ev.packet.pending
            self.assertTrue(istatus)

            yield
            yield from self.idle()
            yield from stim()

        run_simulation(self.dut, padfront(), vcd_name="vcd/%s.vcd" % self.id(), clocks={"usb_48": 4, "sys": 4})

    def _update_internal_signals(self):
        if False:
            yield

    def expect_last_tok(self, epaddr, value):
        endpoint = self.get_endpoint(epaddr)
        last_tok = yield from endpoint.last_tok.read()
        self.assertEqual(last_tok, value)

    ######################################################################
    ## Helpers
    ######################################################################
    def get_endpoint(self, epaddr):
        epdir = EndpointType.epdir(epaddr)
        epnum = EndpointType.epnum(epaddr)
        if epdir == EndpointType.OUT:
            return getattr(self.dut, "ep_%s_out" % epnum)
        elif epdir == EndpointType.IN:
            return getattr(self.dut, "ep_%s_in" % epnum)
        else:
            raise SystemError("Unknown endpoint type: %r" % epdir)

    def pending(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        status = yield from endpoint.ev.pending.read()
        return bool(status & 0x2)

    def dtb(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        status = yield from endpoint.dtb.read()
        return bool(status)

    def trigger(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        status = yield endpoint.ev.packet.trigger
        return bool(status)

    def response(self, epaddr):
        endpoint = self.get_endpoint(epaddr)
        response = yield endpoint.response
        return response

    def set_response(self, epaddr, v):
        endpoint = self.get_endpoint(epaddr)
        assert isinstance(v, EndpointResponse), v
        yield from endpoint.respond.write(v)

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
        endpoint = self.get_endpoint(epaddr)
        yield from endpoint.ev.pending.write(0xf)
        yield
        # Check the pending flag has been cleared
        self.assertFalse((yield from self.trigger(epaddr)))
        self.assertFalse((yield from self.pending(epaddr)))

    def set_data(self, epaddr, data):
        """Set an endpoints buffer to given data to be sent."""
        assert isinstance(data, (list, tuple))
        self.ep_print(epaddr, "Set: %r", data)

        endpoint = self.get_endpoint(epaddr)

        # Make sure the endpoint is empty
        empty = yield from endpoint.ibuf_empty.read()
        self.assertTrue(empty)

        # If we are writing multiple bytes of data, need to make sure we are
        # not going to ACK the packet until the data is ready.
        if len(data) > 1:
            response = yield endpoint.response
            self.assertNotEqual(response, EndpointResponse.ACK)

        for v in data:
            yield from endpoint.ibuf_head.write(v)
            yield

        yield
        yield
        yield
        if len(data) > 0:
            empty = yield from endpoint.ibuf_empty.read()
            self.assertFalse(bool(empty))
        else:
            empty = yield from endpoint.ibuf_empty.read()
            self.assertTrue(bool(empty))

    def expect_data(self, epaddr, data):
        """Expect that an endpoints buffer has given contents."""
        endpoint = self.get_endpoint(epaddr)

        # Make sure there is something pending
        self.assertTrue((yield from self.pending(epaddr)))

        actual_data = []
        while range(0, 1024):
            yield from endpoint.obuf_head.write(0)
            empty = yield from endpoint.obuf_empty.read()
            if empty:
                break

            v = yield from endpoint.obuf_head.read()
            actual_data.append(v)
            yield

        self.ep_print(epaddr, "Got: %r (expected: %r)", actual_data, data)
        self.assertSequenceEqual(data, actual_data)


if __name__ == '__main__':
    unittest.main()
