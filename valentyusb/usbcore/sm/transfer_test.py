#!/usr/bin/env python3

import unittest

from migen import *

from litex.soc.cores.gpio import GPIOOut

from ..endpoint import *
from ..io import FakeIoBuf
from ..pid import PIDTypes
from ..rx.pipeline import RxPipeline
from ..tx.pipeline import TxPipeline
from .header import PacketHeaderDecode
from .send import TxPacketSend

from ..utils.packet import *
from ..test.common import BaseUsbTestCase, CommonUsbTestCase
from ..test.clock import CommonTestMultiClockDomain

from .transfer import UsbTransfer


class TestUsbTransfer(
        BaseUsbTestCase,
        CommonUsbTestCase,
        CommonTestMultiClockDomain,
        unittest.TestCase):

    maxDiff=None

    def setUp(self):
        CommonTestMultiClockDomain.setUp(self, ("usb_12", "usb_48"))

        self.iobuf = FakeIoBuf()
        self.dut = UsbTransfer(self.iobuf)

        self.packet_h2d = Signal(1)
        self.packet_d2h = Signal(1)
        self.packet_idle = Signal(1)

        class Endpoint:
            def __init__(self):
                self._response = EndpointResponse.NAK
                self.trigger = False
                self.pending = True
                self.data = None
                self.dtb = True

            def update(self):
                if self.trigger:
                    self.pending = True

            @property
            def response(self):
                if self._response == EndpointResponse.ACK and (self.pending or self.trigger):
                    return EndpointResponse.NAK
                else:
                    return self._response

            @response.setter
            def response(self, v):
                assert isinstance(v, EndpointResponse), repr(v)
                self._response = v

            def __str__(self):
                data = self.data
                if data is None:
                    data = []
                return "<Endpoint p:(%s,%s) %s d:%s>" % (
                    int(self.trigger), int(self.pending), int(self.dtb), len(data))

        self.endpoints = {
            EndpointType.epaddr(0, EndpointType.OUT): Endpoint(),
            EndpointType.epaddr(0, EndpointType.IN):  Endpoint(),
            EndpointType.epaddr(1, EndpointType.OUT): Endpoint(),
            EndpointType.epaddr(1, EndpointType.IN):  Endpoint(),
            EndpointType.epaddr(2, EndpointType.OUT): Endpoint(),
            EndpointType.epaddr(2, EndpointType.IN):  Endpoint(),
        }
        for epaddr in self.endpoints:
            self.endpoints[epaddr].addr = epaddr

    def run_sim(self, stim):
        def padfront():
            yield self.packet_h2d.eq(0)
            yield self.packet_d2h.eq(0)
            yield self.packet_idle.eq(0)
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
            clocks={"sys": 12, "usb_48": 48, "usb_12": 192},
        )
        print("-"*10)

    def tick_sys(self):
        self.update_internal_signals()
        yield

    def tick_usb48(self):
        yield from self.wait_for_edge("usb_48")

    def tick_usb12(self):
        for i in range(0, 4):
            yield from self.wait_for_edge("usb_48")

    def on_usb_48_edge(self):
        if False:
            yield

    def on_usb_12_edge(self):
        dut = self.dut

        # These should only change on usb12 edge
        start = yield dut.start
        setup = yield dut.setup
        commit = yield dut.commit
        abort = yield dut.abort
        end = yield dut.end

        tok = yield dut.tok
        addr = yield dut.addr
        endp = yield dut.endp

        if endp > 2:
            return

        # -----

        # Host->Device pathway
        oep = self.endpoints[EndpointType.epaddr(endp, EndpointType.OUT)]
        data_recv_put = yield dut.data_recv_put
        if data_recv_put:
            data_recv_payload = yield dut.data_recv_payload
            if oep.data is None:
                oep.data = []
            oep.data.append(data_recv_payload)

        # Host->Device State flags
        if tok == PID.OUT or tok == PID.SETUP:
            if oep.response == EndpointResponse.STALL:
                yield dut.sta.eq(1)
            else:
                yield dut.sta.eq(0)

            if oep.response == EndpointResponse.NAK:
                yield dut.arm.eq(0)
            if oep.response == EndpointResponse.ACK:
                yield dut.arm.eq(1)

            if commit:
                oep.dtb = not oep.dtb
                oep.trigger = True

        # -----

        # Device->Host pathway
        iep = self.endpoints[EndpointType.epaddr(endp, EndpointType.IN)]
        data_send_get = yield dut.data_send_get

        if iep.data:
            yield dut.data_send_payload.eq(iep.data[0])
        else:
            yield dut.data_send_payload.eq(0xff)

        if not iep.pending and iep.data:
            if data_send_get:
                iep.data.pop(0)
            yield dut.data_send_have.eq(len(iep.data) > 0)
        else:
            yield dut.data_send_have.eq(0)
            self.assertFalse(data_send_get)

        # Device->Host State flags
        if tok == PID.IN:
            if iep.response == EndpointResponse.STALL:
                yield dut.sta.eq(1)
            else:
                yield dut.sta.eq(0)

            if iep.response == EndpointResponse.NAK:
                yield dut.arm.eq(0)
            if iep.response == EndpointResponse.ACK:
                yield dut.arm.eq(1)

            yield dut.dtb.eq(iep.dtb)

            if commit:
                iep.dtb = not iep.dtb
                iep.trigger = True

        # -----

        # Special setup stuff...
        if setup:
            oep.response = EndpointResponse.NAK
            oep.dtb = True

            iep.response = EndpointResponse.NAK
            iep.dtb = True

    def update_internal_signals(self):
        for ep in self.endpoints.values():
            if ep.trigger:
                ep.pending = True
                ep.trigger = False
        del ep

        yield from self.update_clocks()

    ######################################################################
    ## Helpers
    ######################################################################

    # IRQ / packet pending -----------------
    def trigger(self, epaddr):
        yield from self.update_internal_signals()
        return self.endpoints[epaddr].trigger

    def pending(self, epaddr):
        yield from self.update_internal_signals()
        return self.endpoints[epaddr].pending or self.endpoints[epaddr].trigger

    def clear_pending(self, epaddr):
        # Can't clear pending while trigger is active.
        trigger = (yield from self.trigger(epaddr))
        #for i in range(0, 100):
        #    trigger = (yield from self.trigger(epaddr))
        #    if not trigger:
        #        break
        #    yield
        self.assertFalse(trigger)

        # Check the pending flag is raised
        self.assertTrue((yield from self.pending(epaddr)))

        # Clear pending flag
        self.endpoints[epaddr].pending = False
        self.ep_print(epaddr, "clear_pending")

        # Check the pending flag has been cleared
        self.assertFalse((yield from self.trigger(epaddr)))
        self.assertFalse((yield from self.pending(epaddr)))

    # Endpoint state -----------------------
    def response(self, epaddr):
        if False:
            yield
        return self.endpoints[epaddr].response

    def set_response(self, epaddr, v):
        assert isinstance(v, EndpointResponse), v
        if False:
            yield
        self.ep_print(epaddr, "set_response: %s", v)
        self.endpoints[epaddr].response = v

    # Get/set endpoint data ----------------
    def set_data(self, epaddr, data):
        """Set an endpoints buffer to given data to be sent."""
        if False:
            yield

        assert isinstance(data, (list, tuple))
        if not isinstance(data, list):
            data = list(data)

        self.ep_print(epaddr, "set_data: %r", data)
        self.endpoints[epaddr].data = data

    def expect_data(self, epaddr, data):
        """Expect that an endpoints buffer has given contents."""
        # Make sure there is something pending
        for i in range(10):
            pending = yield from self.pending(epaddr)
            if pending:
                break
            yield from self.tick_usb48()
        self.assertTrue(pending)

        self.ep_print(epaddr, "expect_data: %s", data)
        actual_data = self.endpoints[epaddr].data
        assert actual_data is not None
        self.endpoints[epaddr].data = None

        # Strip the last two bytes which contain the CRC16
        assert len(actual_data) >= 2, actual_data
        actual_data, actual_crc = actual_data[:-2], actual_data[-2:]

        self.ep_print(epaddr, "Got: %r (expected: %r)", actual_data, data)
        self.assertSequenceEqual(data, actual_data)
        self.assertSequenceEqual(crc16(data), actual_crc)

    def dtb(self, epaddr):
        if False:
            yield
        print("dtb", epaddr, self.endpoints[epaddr])
        return self.endpoints[epaddr].dtb



if __name__ == "__main__":
    unittest.main()
