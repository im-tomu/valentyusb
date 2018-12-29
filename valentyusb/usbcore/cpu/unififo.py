#!/usr/bin/env python3

from migen import *
from migen.genlib import fifo
from migen.genlib import cdc

from litex.soc.interconnect import csr_eventmanager as ev
from litex.soc.interconnect.csr import *
from litex.soc.cores.gpio import GPIOOut

from ..endpoint import *
from ..io import FakeIoBuf
from ..rx.pipeline import RxPipeline
from ..tx.pipeline import TxPipeline

from ..test.common import CommonUsbTestCase


class UsbUniFifo(Module, AutoCSR):
    """
    Presents the USB data stream as two FIFOs via CSR registers.
    """

    def __init__(self, iobuf):
        self.submodules.ev = ev.EventManager()
        self.ev.submodules.rx = ev.EventSourcePulse()

        # ---------------------
        # RX side
        # ---------------------
        self.submodules.rx = rx = RxPipeline()

        obuf = fifo.AsyncFIFOBuffered(width=8, depth=128)
        self.submodules.obuf = ClockDomainsRenamer({"write": "usb_12", "read": "sys"})(obuf)

        # USB side (writing)
        self.comb += [
            self.obuf.din.eq(self.rx.o_data_payload),
            self.obuf.we.eq(self.rx.o_data_strobe),
            self.ev.rx.trigger.eq(self.rx.o_pkt_end),
        ]

        # System side (reading)
        self.submodules.obuf_head = CSR(8)
        self.submodules.obuf_empty = CSRStatus(1)
        self.comb += [
            self.obuf_head.w.eq(self.obuf.dout),
            self.obuf.re.eq(self.obuf_head.re),
            self.obuf_empty.status.eq(~self.obuf.readable),
        ]

        # ---------------------
        # TX side
        # ---------------------
        self.submodules.tx = tx = TxPipeline()

        ibuf = fifo.AsyncFIFOBuffered(width=8, depth=128)
        self.submodules.ibuf = ClockDomainsRenamer({"write": "sys", "read": "usb_12"})(ibuf)

        # System side (writing)
        self.submodules.arm = CSRStorage(1)
        self.submodules.ibuf_head = CSR(8)
        self.submodules.ibuf_empty = CSRStatus(1)
        self.comb += [
            self.ibuf.din.eq(self.ibuf_head.r),
            self.ibuf.we.eq(self.ibuf_head.re),
            self.ibuf_empty.status.eq(~self.ibuf.readable & ~tx.o_oe),
        ]

        # USB side (reading)
        self.comb += [
            tx.i_oe.eq(self.ibuf.readable & self.arm.storage),
            tx.i_data_payload.eq(self.ibuf.dout),
            self.ibuf.re.eq(tx.o_data_strobe & self.arm.storage),
        ]

        # ----------------------
        # Tristate
        # ----------------------
        self.submodules.iobuf = iobuf
        self.comb += [
            iobuf.usb_tx_en.eq(tx.o_oe),
            iobuf.usb_p_tx.eq(tx.o_usbp),
            iobuf.usb_n_tx.eq(tx.o_usbn),
        ]
        self.submodules.pullup = GPIOOut(iobuf.usb_pullup)


class TestUsbDeviceSimple(CommonUsbTestCase):

    maxDiff=None

    def setUp(self):
        self.iobuf = FakeIoBuf()
        self.dut = UsbUniFifo(self.iobuf)

        self.states = [
            "WAIT",
            "RECV_DATA",
            "SEND_HAND",
            "SEND_DATA",
            "RECV_HAND",
        ]
        self.decoding = dict(enumerate(self.states))
        self.dut.state = Signal(max=len(self.states))
        self.dut.state._enumeration = self.decoding

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

    def run_sim(self, stim):
        def padfront():
            yield from self.next_state("WAIT")
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
        run_simulation(self.dut, padfront(), vcd_name="vcd/%s.vcd" % self.id(), clocks={"usb_48": 4, "sys": 4})
        print("-"*10)

    def recv_packet(self):
        rx = (yield from self.dut.ev.pending.read()) & 0b1
        if not rx:
            return

        actual_data = []
        while range(0, 1024):
            yield from self.dut.obuf_head.write(0)
            empty = yield from self.dut.obuf_empty.read()
            if empty:
                break

            v = yield from self.dut.obuf_head.read()
            actual_data.append(v)
            yield

        yield from self.dut.ev.pending.write(0b1)
        yield

        #self.assertEqual(actual_data[0], 0b00000001)
        return actual_data

    def send_packet(self, pid, data=None):
        yield from self.dut.arm.write(0)
        armed = yield from self.dut.arm.read()
        self.assertFalse(armed)

        empty = yield from self.dut.obuf_empty.read()
        self.assertTrue(empty)

        #           sync,       pid
        pkt_data = [0b10000000, pid | ((0b1111 ^ pid) << 4)]
        if data is None:
            assert pid in (PID.ACK, PID.NAK, PID.STALL), (pid, data)
        else:
            assert pid in (PID.DATA0, PID.DATA1), pid
            pkt_data += data
            pkt_data += crc16(data)

        print("send_packet", pid, data)
        print("send_packet", pkt_data)
        for d in pkt_data:
            yield from self.dut.ibuf_head.write(d)
            yield
            yield
            yield
            yield

        empty = yield from self.dut.ibuf_empty.read()
        self.assertFalse(empty)

        yield from self.dut.arm.write(1)

    def next_state(self, state):
        self.assertIn(state, self.states)
        yield self.dut.state.eq(self.states.index(state))
        self.state = state

    def _update_internal_signals(self):
        def decode_pid(pkt_data):
            pkt_data = encode_data(pkt_data[:1])
            pidt = int(pkt_data[0:4][::-1], 2)
            pidb = int(pkt_data[4:8][::-1], 2)
            self.assertEqual(pidt ^ 0b1111, pidb)
            return PID(pidt)

        for ep in self.endpoints.values():
            if ep.trigger:
                ep.pending = True
                ep.trigger = False
        del ep

        if self.state == "WAIT":
            self.ep = None
            self.handshake = None

            pkt_data = yield from self.recv_packet()
            if not pkt_data:
                return

            self.assertEqual(len(pkt_data), 3, pkt_data)
            pid = decode_pid(pkt_data)
            pkt_data = encode_data(pkt_data)
            addr = int(pkt_data[8:8+7][::-1], 2)
            endp = int(pkt_data[8+7:8+7+4][::-1], 2)
            crc5 = int(pkt_data[8+7+4:][::-1], 2)

            print("WAIT      pid:", pid, "addr:", addr, "ep:", endp, "crc5:", crc5)
            self.assertEqual(crc5, crc5_token(addr, endp))

            if pid == PID.SETUP or pid == PID.OUT:
                self.ep = self.endpoints[EndpointType.epaddr(endp, EndpointType.OUT)]
                if pid == PID.SETUP:
                    self.handshake = EndpointResponse.ACK
                    self.ep.response = EndpointResponse.NAK
                    self.ep.dtb = False

                    iep = self.endpoints[EndpointType.epaddr(endp, EndpointType.IN)]
                    self.assertIsNot(self.ep, iep)
                    iep.response = EndpointResponse.NAK
                    iep.dtb = True
                    print(self.ep, iep)
                else:
                    self.handshake = self.ep.response
                yield from self.next_state("RECV_DATA")

            elif pid == PID.IN:
                self.ep = self.endpoints[EndpointType.epaddr(endp, EndpointType.IN)]
                self.handshake = self.ep.response

                if self.ep.response == EndpointResponse.ACK:
                    #self.assertIsNotNone(self.ep.data)
                    if self.ep.data is None:
                        self.ep.data = []
                    yield from self.next_state("SEND_DATA")
                else:
                    yield from self.next_state("SEND_HAND")
            else:
                assert False, pid

        elif self.state == "RECV_DATA":
            self.assertIsNotNone(self.ep)
            pkt_data = yield from self.recv_packet()
            if not pkt_data:
                return

            pid = decode_pid(pkt_data)
            print("RECV_DATA pid:", pid, "data:", pkt_data)

            if self.handshake == EndpointResponse.ACK:
                self.assertIsNone(self.ep.data)
                self.assertIn(encode_pid(pid), (encode_pid(PID.DATA0), encode_pid(PID.DATA1)))
                self.assertSequenceEqual(pkt_data[-2:], crc16(pkt_data[1:-2]))
                self.ep.data = pkt_data[1:-2]

            yield from self.next_state("SEND_HAND")

        elif self.state == "SEND_HAND":
            self.assertIsNotNone(self.ep)
            self.assertIsNotNone(self.handshake)
            pid = {
                EndpointResponse.STALL: PID.STALL,
                EndpointResponse.NAK:   PID.NAK,
                EndpointResponse.ACK:   PID.ACK,
            }[self.handshake]
            print("SEND_HAND pid:", pid)
            yield from self.send_packet(pid)
            if self.handshake == EndpointResponse.ACK:
                self.ep.trigger = True
                self.ep.dtb = not self.ep.dtb
            yield from self.next_state("WAIT")

        elif self.state == "SEND_DATA":
            self.assertIsNotNone(self.ep)
            self.assertIsNotNone(self.ep.data)
            pid = [PID.DATA0, PID.DATA1][self.ep.dtb]
            print("SEND_DATA pid:", pid, "data:", self.ep.data)
            yield from self.send_packet(pid, self.ep.data)
            self.ep.data = None
            yield from self.next_state("RECV_HAND")

        elif self.state == "RECV_HAND":
            self.assertIsNotNone(self.ep)
            pkt_data = yield from self.recv_packet()
            if not pkt_data:
                return

            pid = decode_pid(pkt_data)
            print("RECV_HAND pid:", pid)
            if pid != PID.ACK:
                raise SystemError(pkt_data)

            self.ep.trigger = True
            self.ep.dtb = not self.ep.dtb

            yield from self.next_state("WAIT")

    ######################################################################
    ## Helpers
    ######################################################################

    # IRQ / packet pending -----------------
    def trigger(self, epaddr):
        yield from self._update_internal_signals()
        return self.endpoints[epaddr].trigger

    def pending(self, epaddr):
        yield from self._update_internal_signals()
        return self.endpoints[epaddr].pending

    def clear_pending(self, epaddr):
        # Can't clear pending while trigger is active.
        for i in range(0, 100):
            trigger = (yield from self.trigger(epaddr))
            if not trigger:
                break
            yield
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
        assert isinstance(data, (list, tuple))
        if False:
            yield

        self.ep_print(epaddr, "Set: %r", data)
        self.endpoints[epaddr].data = data

    def expect_data(self, epaddr, data):
        """Expect that an endpoints buffer has given contents."""
        # Make sure there is something pending
        self.assertTrue((yield from self.pending(epaddr)))

        self.ep_print(epaddr, "expect_data: %s", data)
        actual_data = self.endpoints[epaddr].data
        assert actual_data is not None
        self.endpoints[epaddr].data = None

        self.ep_print(epaddr, "Got: %r (expected: %r)", actual_data, data)
        self.assertSequenceEqual(data, actual_data)

    def dtb(self, epaddr):
        if False:
            yield
        print("dtb", epaddr, self.endpoints[epaddr])
        return self.endpoints[epaddr].dtb


if __name__ == "__main__":
    import unittest
    unittest.main()
