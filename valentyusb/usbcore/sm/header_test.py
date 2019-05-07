#!/usr/bin/env python3

import unittest

from migen import *

from ..pid import PIDTypes
from ..rx.pipeline import RxPipeline
from ..utils.packet import *

from ..test.common import BaseUsbTestCase

from .header import PacketHeaderDecode


class TestPacketHeaderDecode(BaseUsbTestCase):

    def sim(self, stim):
        rx = RxPipeline()
        dut = PacketHeaderDecode(rx)

        run_simulation(
            dut, stim(dut),
            vcd_name=self.make_vcd_name(),
            clocks={"sys": 12, "usb_48": 48, "usb_12": 192},
        )

    def recv_packet(self, dut, bits, tick):
        if not tick:
            def tick():
                if False:
                    yield

        for i in range(len(bits)):
            b = bits[i]
            if b == ' ':
                continue
            elif b == '_':
                # SE0 - both lines pulled low
                yield dut.rx.i_usbp.eq(0)
                yield dut.rx.i_usbn.eq(0)
            elif b == 'J':
                yield dut.rx.i_usbp.eq(1)
                yield dut.rx.i_usbn.eq(0)
            elif b == 'K':
                yield dut.rx.i_usbp.eq(0)
                yield dut.rx.i_usbn.eq(1)
            else:
                assert False, "Unknown value: %s" % v

            # four 48MHz cycles = 1 bit time
            for t in range(0, 4):
                yield

            continue_sim = yield from tick(dut)
            if not continue_sim:
                break

        MAX_ITER=10000
        if continue_sim:
            for i in range(0, MAX_ITER):
                continue_sim = yield from tick(dut)
                if not continue_sim:
                    break
                yield
        self.assertFalse(continue_sim)
        self.assertLess(i, MAX_ITER-1)

    def check_packet(self, expected_pid, expected_addr, expected_endp, packet):
        def stim(dut):
            for i in range(100):
                yield

            def tick(dut):
                return not (yield dut.o_decoded)

            yield from self.recv_packet(
                dut,
                packet,
                tick,
            )

            decoded = yield dut.o_decoded
            self.assertTrue(decoded)

            actual_pid = yield dut.o_pid
            self.assertEqual(expected_pid, actual_pid)

            actual_addr = yield dut.o_addr
            self.assertEqual(expected_addr, actual_addr)

            actual_endp = yield dut.o_endp
            self.assertEqual(expected_endp, actual_endp)

            for i in range(100):
                yield

        self.sim(stim)

    def check_token(self, expected_pid, expected_addr, expected_endp):
        self.check_packet(
            expected_pid, expected_addr, expected_endp,
            wrap_packet(token_packet(expected_pid, expected_addr, expected_endp)),
        )

    def check_data(self, expected_pid, data):
        self.check_packet(
            expected_pid, 0, 0,
            wrap_packet(data_packet(expected_pid, data)),
        )

    def check_status(self, expected_pid):
        # Status packet is a data_packet with no data.
        self.check_packet(
            expected_pid, 0, 0,
            wrap_packet(data_packet(expected_pid, [])),
        )

    def check_handshake(self, expected_pid):
        self.check_packet(
            expected_pid, 0, 0,
            wrap_packet(handshake_packet(expected_pid)),
        )

    def test_decode_setup_zero(self):
        self.check_token(PID.SETUP, 0x0, 0x0)

    def test_decode_in_ep1(self):
        self.check_token(PID.IN, 28, 1)

    def test_decode_out_ep8(self):
        self.check_token(PID.OUT, 12, 0xf)

    def test_decode_data0(self):
        self.check_status(PID.DATA0)

    def test_decode_data1(self):
        self.check_status(PID.DATA1)

    def test_decode_ack(self):
        self.check_handshake(PID.ACK)

    def test_decode_nak(self):
        self.check_handshake(PID.NAK)


if __name__ == "__main__":
    unittest.main()
