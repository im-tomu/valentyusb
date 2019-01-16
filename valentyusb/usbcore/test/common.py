#!/usr/bin/env python3

import unittest

from itertools import zip_longest

from ..endpoint import *
from ..pid import *
from ..utils.asserts import assertMultiLineEqualSideBySide
from ..utils.packet import *
from ..utils.pprint import pp_packet


def grouper(n, iterable, pad=None):
    """Group iterable into multiples of n (with optional padding).

    >>> list(grouper(3, 'abcdefg', 'x'))
    [('a', 'b', 'c'), ('d', 'e', 'f'), ('g', 'x', 'x')]

    """
    return zip_longest(*[iter(iterable)]*n, fillvalue=pad)


class CommonUsbTestCase(unittest.TestCase):
    maxDiff=None

    def assertMultiLineEqualSideBySide(self, data1, data2, msg):
        return assertMultiLineEqualSideBySide(data1, data2, msg)

    def ep_print(self, epaddr, msg, *args):
        print("ep(%i, %s): %s" % (
            EndpointType.epnum(epaddr),
            EndpointType.epdir(epaddr).name,
            msg) % args)

    ######################################################################
    # Helper methods
    # FIXME: Should these be marked as internal only?
    ######################################################################
    def idle(self, cycles=10):
        yield self.packet_idle.eq(1)
        yield from self.dut.iobuf.recv('I')
        for i in range(0, cycles):
            yield
        yield self.packet_idle.eq(0)

    # Host->Device
    def _send_packet(self, packet):
        """Send a USB packet."""
        #print("_send_packet(", packet, ")")
        packet = wrap_packet(packet)
        for v in packet:
            yield from self._update_internal_signals()
            yield from self.dut.iobuf.recv(v)
            yield
        yield from self._update_internal_signals()
        for i in range(0, 100):
            yield

    def send_token_packet(self, pid, addr, epaddr):
        epnum = EndpointType.epnum(epaddr)
        yield self.packet_h2d.eq(1)
        yield from self._send_packet(token_packet(pid, addr, epnum))
        yield self.packet_h2d.eq(0)

    def send_data_packet(self, pid, data):
        assert pid in (PID.DATA0, PID.DATA1), pid
        yield self.packet_h2d.eq(1)
        yield from self._send_packet(data_packet(pid, data))
        yield self.packet_h2d.eq(0)

    def send_ack(self):
        yield self.packet_h2d.eq(1)
        yield from self._send_packet(handshake_packet(PID.ACK))
        yield self.packet_h2d.eq(0)

    def send_nak(self):
        yield self.packet_h2d.eq(1)
        yield from self._send_packet(handshake_packet(PID.NAK))
        yield self.packet_h2d.eq(0)

    # Device->Host
    def expect_packet(self, packet, msg=None):
        """Except to receive the following USB packet."""
        yield self.packet_d2h.eq(1)

        # Wait for transmission to happen
        yield from self.dut.iobuf.recv('I')
        tx = 0
        for i in range(0, 100):
            yield from self._update_internal_signals()
            tx = yield self.dut.iobuf.usb_tx_en
            if tx:
                break
            yield
        self.assertTrue(tx, "No packet started, "+msg)

        # Read in the packet data
        result = ""
        for i in range(0, 2048):
            yield from self._update_internal_signals()

            result += yield from self.iobuf.current()
            yield
            tx = yield self.dut.iobuf.usb_tx_en
            if not tx:
                break
        self.assertFalse(tx, "Packet didn't finish, "+msg)
        yield self.packet_d2h.eq(0)

        # Check the packet received matches
        expected = pp_packet(wrap_packet(packet))
        actual = pp_packet(result)
        self.assertMultiLineEqualSideBySide(expected, actual, msg)

    # No expect_token_packet, as the host is the only one who generates tokens.

    def expect_data_packet(self, pid, data):
        assert pid in (PID.DATA0, PID.DATA1), pid
        yield self.packet_d2h.eq(1)
        yield from self.expect_packet(data_packet(pid, data), "Expected %s packet with %r" % (pid.name, data))
        yield self.packet_d2h.eq(0)

    def expect_ack(self):
        yield self.packet_d2h.eq(1)
        yield from self.expect_packet(handshake_packet(PID.ACK), "Expected ACK packet.")
        yield self.packet_d2h.eq(0)

    def expect_nak(self):
        yield self.packet_d2h.eq(1)
        yield from self.expect_packet(handshake_packet(PID.NAK), "Expected NAK packet.")
        yield self.packet_d2h.eq(0)

    def expect_stall(self):
        yield self.packet_d2h.eq(1)
        yield from self.expect_packet(handshake_packet(PID.STALL), "Expected STALL packet.")
        yield self.packet_d2h.eq(0)

    def expect_last_tok(self, epaddr, value):
        if False:
            yield

    def check_pending(self, epaddr):
        # Check no pending packets
        self.assertFalse((yield from self.pending(epaddr)))

    def check_pending_and_response(self, epaddr):
        yield from self.check_pending(epaddr)
        # Check we are going to ack the packets
        self.assertEqual((yield from self.response(epaddr)), EndpointResponse.ACK)

    # Full transactions
    # ->token  ->token
    # <-data   ->data
    # ->ack    <-ack

    # Host to Device
    # ->setup
    # ->data0[...]
    # <-ack
    def transaction_setup(self, addr, data):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

        yield from self.send_token_packet(PID.SETUP, addr, epaddr_out)
        yield from self.send_data_packet(PID.DATA0, data)
        yield from self.expect_ack()
        yield from self.expect_data(epaddr_out, data)
        yield from self.clear_pending(epaddr_out)

        # Check nothing pending at the end
        self.assertFalse((yield from self.pending(epaddr_out)))

        # Check the token is set correctly
        yield from self.expect_last_tok(epaddr_out, 0b11)

        # Check the in/out endpoint is reset to NAK
        self.assertEqual((yield from self.response(epaddr_out)), EndpointResponse.NAK)
        self.assertEqual((yield from self.response(epaddr_in)), EndpointResponse.NAK)

    # Host to Device
    # ->out
    # ->data0[...]
    # <-ack
    # ->out
    # ->data1[...]
    # <-ack
    # ....
    def transaction_data_out(self, addr, epaddr, data, chunk_size=8):
        yield from self.check_pending_and_response(epaddr)

        datax = PID.DATA0
        for i, chunk in enumerate(grouper(chunk_size, data, pad=0)):
            self.assertFalse((yield from self.pending(epaddr)))
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(datax, chunk)
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, chunk)
            yield from self.clear_pending(epaddr)

            yield from self.expect_last_tok(epaddr, 0b00)
            if datax == PID.DATA0:
                datax = PID.DATA1
            else:
                datax = PID.DATA0

        # Check nothing pending at the end
        self.assertFalse((yield from self.pending(epaddr)))

    # Host to Device
    # ->out
    # ->data1[]
    # <-ack
    def transaction_status_out(self, addr, epaddr):
        assert EndpointType.epdir(epaddr) == EndpointType.OUT
        yield from self.check_pending_and_response(epaddr)

        yield from self.send_token_packet(PID.OUT, addr, epaddr)
        yield from self.send_data_packet(PID.DATA1, [])
        yield from self.expect_ack()
        yield from self.expect_data(epaddr, [])
        yield from self.clear_pending(epaddr)

        # Check nothing pending at the end
        self.assertFalse((yield from self.pending(epaddr)))

    # Device to Host
    # ->in
    # <-data0[...]
    # ->ack
    # ->in
    # <-data1[...]
    # ->ack
    # ....
    def transaction_data_in(self, addr, epaddr, data, chunk_size=8, dtb=PID.DATA1):
        assert EndpointType.epdir(epaddr) == EndpointType.IN

        datax = dtb
        for i, chunk in enumerate(grouper(chunk_size, data, pad=0)):
            yield from self.check_pending_and_response(epaddr)
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield from self.set_data(epaddr, chunk)
            yield from self.set_response(epaddr, EndpointResponse.ACK)

            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(datax, chunk)
            yield from self.send_ack()
            yield from self.clear_pending(epaddr)

            yield from self.expect_last_tok(epaddr, 0b10)
            if datax == PID.DATA0:
                datax = PID.DATA1
            else:
                datax = PID.DATA0

        # Check nothing pending at the end
        self.assertFalse((yield from self.pending(epaddr)))

    # Device to Host
    # ->in
    # <-data1[]
    # ->ack
    def transaction_status_in(self, addr, epaddr):
        assert EndpointType.epdir(epaddr) == EndpointType.IN
        yield from self.check_pending_and_response(epaddr)

        yield from self.set_data(epaddr, [])
        yield from self.send_token_packet(PID.IN, addr, epaddr)
        yield from self.expect_data_packet(PID.DATA1, [])
        yield from self.send_ack()
        yield from self.clear_pending(epaddr)

        # Check nothing pending at the end
        self.assertFalse((yield from self.pending(epaddr)))

    # Full control transfer
    ########################
    def control_transfer_in(self, addr, setup_data, descriptor_data):
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)

        yield from self.check_pending(epaddr_in)
        yield from self.check_pending(epaddr_out)

        # Setup stage
        yield from self.transaction_setup(addr, setup_data)

        yield from self.check_pending(epaddr_in)
        yield from self.check_pending(epaddr_out)

        # Data stage
        yield from self.set_response(epaddr_in, EndpointResponse.ACK)
        yield from self.transaction_data_in(addr, epaddr_in, descriptor_data)

        yield from self.check_pending(epaddr_in)
        yield from self.check_pending(epaddr_out)

        # Status stage
        yield from self.set_response(epaddr_out, EndpointResponse.ACK)
        yield from self.transaction_status_out(addr, epaddr_out)

        yield from self.check_pending(epaddr_in)
        yield from self.check_pending(epaddr_out)

    def control_transfer_out(self, addr, setup_data, descriptor_data):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
        # Setup stage
        yield from self.transaction_setup(addr, setup_data)
        # Data stage
        yield from self.set_response(epaddr_out, EndpointResponse.ACK)
        yield from self.transaction_data_out(addr, epaddr_out, descriptor_data)
        # Status stage
        yield from self.set_response(epaddr_in, EndpointResponse.ACK)
        yield from self.transaction_status_in(addr, epaddr_in)

    ######################################################################
    # Interface subclasses need to implement.
    ######################################################################

    def run_sim(self, stim):
        raise NotImplementedError

    # IRQ / packet pending -----------------
    def trigger(self, epaddr):
        raise NotImplementedError

    def clear_pending(self, epaddr):
        raise NotImplementedError

    def pending(self, epaddr):
        raise NotImplementedError

    # Endpoint state -----------------------
    def response(self, epaddr):
        raise NotImplementedError

    def set_response(self, epaddr, v):
        raise NotImplementedError

    # Get/set endpoint data ----------------
    def set_data(self, epaddr, data):
        raise NotImplementedError

    def expect_data(self, epaddr, data):
        raise NotImplementedError

    ######################################################################
    # Actual test cases are after here.
    ######################################################################

    def test_control_setup(self):
        def stim():
            #   012345   0123
            # 0b011100 0b1000
            yield from self.transaction_setup(28, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])
        self.run_sim(stim)

    def test_control_setup_clears_stall(self):
        def stim():
            addr = 28
            epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)

            d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8]

            yield from self.clear_pending(epaddr_out)
            yield from self.set_response(epaddr_out, EndpointResponse.ACK)
            yield

            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA0, d[:4])
            yield from self.expect_ack()
            yield from self.expect_data(epaddr_out, d[:4])

            yield from self.set_response(epaddr_out, EndpointResponse.STALL)

            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA0, d[4:])
            yield from self.expect_stall()

            yield from self.send_token_packet(PID.SETUP, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_ack()

            yield
            respond = yield from self.response(epaddr_out)
            self.assertEqual(respond, EndpointResponse.NAK)
            yield

            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA0, d[:4])
            yield from self.expect_nak()

        self.run_sim(stim)

    def test_control_transfer_in(self):
        def stim():
            yield from self.clear_pending(EndpointType.epaddr(0, EndpointType.OUT))
            yield from self.clear_pending(EndpointType.epaddr(0, EndpointType.IN))
            yield

            yield from self.control_transfer_in(
                20,
                # Get descriptor, Index 0, Type 03, LangId 0000, wLength 10?
                [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00],
                # 12 byte descriptor, max packet size 8 bytes
                [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                 0x08, 0x09, 0x0A, 0x0B],
            )
        self.run_sim(stim)

    def test_control_transfer_in_nak_data(self):
        def stim():
            addr = 22
            # Get descriptor, Index 0, Type 03, LangId 0000, wLength 64
            setup_data = [0x80, 0x06, 0x00, 0x03, 0x00, 0x00, 0x40, 0x00]
            in_data = [0x04, 0x03, 0x09, 0x04]

            epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
            yield from self.clear_pending(epaddr_in)

            # Setup stage
            # -----------
            yield from self.transaction_setup(addr, setup_data)

            # Data stage
            # -----------
            yield from self.set_response(epaddr_in, EndpointResponse.NAK)
            yield from self.send_token_packet(PID.IN, addr, epaddr_in)
            yield from self.expect_nak()

            yield from self.set_data(epaddr_in, in_data)
            yield from self.set_response(epaddr_in, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr_in)
            yield from self.expect_data_packet(PID.DATA1, in_data)
            yield from self.send_ack()
            yield from self.clear_pending(epaddr_in)

        self.run_sim(stim)

    def test_control_transfer_in_nak_status(self):
        def stim():
            addr = 20
            setup_data = [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00]
            out_data = [0x00, 0x01]

            epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
            epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
            yield from self.clear_pending(epaddr_out)
            yield from self.clear_pending(epaddr_in)

            # Setup stage
            # -----------
            yield from self.transaction_setup(addr, setup_data)

            # Data stage
            # ----------
            yield from self.set_response(epaddr_out, EndpointResponse.ACK)
            yield from self.transaction_data_out(addr, epaddr_out, out_data)

            # Status stage
            # ----------
            yield from self.set_response(epaddr_in, EndpointResponse.NAK)

            yield from self.send_token_packet(PID.IN, addr, epaddr_in)
            yield from self.expect_nak()

            yield from self.send_token_packet(PID.IN, addr, epaddr_in)
            yield from self.expect_nak()

            yield from self.set_response(epaddr_in, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr_in)
            yield from self.expect_data_packet(PID.DATA1, [])
            yield from self.send_ack()
            yield from self.clear_pending(epaddr_in)

        self.run_sim(stim)

    def test_control_transfer_out(self):
        def stim():
            yield from self.clear_pending(EndpointType.epaddr(0, EndpointType.OUT))
            yield from self.clear_pending(EndpointType.epaddr(0, EndpointType.IN))
            yield

            yield from self.control_transfer_out(
                20,
                # Get descriptor, Index 0, Type 03, LangId 0000, wLength 10?
                [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00],
                # 12 byte descriptor, max packet size 8 bytes
                [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                 0x08, 0x09, 0x0A, 0x0B],
            )
        self.run_sim(stim)

    def test_control_transfer_out_nak_data(self):
        def stim():
            addr = 20
            setup_data = [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00]
            out_data = [
                0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                0x08, 0x09, 0x0A, 0x0B,
            ]

            epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
            yield from self.clear_pending(epaddr_out)

            # Setup stage
            # -----------
            yield from self.transaction_setup(addr, setup_data)

            # Data stage
            # ----------
            yield from self.set_response(epaddr_out, EndpointResponse.NAK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, out_data)
            yield from self.expect_nak()

            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, out_data)
            yield from self.expect_nak()

            #for i in range(200):
            #    yield

            yield from self.set_response(epaddr_out, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, out_data)
            yield from self.expect_ack()
            yield from self.expect_data(epaddr_out, out_data)
            yield from self.clear_pending(epaddr_out)


        self.run_sim(stim)

    def test_control_transfer_out_nak_status(self):
        def stim():
            addr = 20
            setup_data = [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00]
            descriptor_data = [
                0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
                0x08, 0x09, 0x0A, 0x0B,
            ]

            epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
            epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
            yield from self.clear_pending(epaddr_out)
            yield from self.clear_pending(epaddr_in)
            yield

            # Setup stage
            # -----------
            yield from self.transaction_setup(addr, setup_data)

            # Data stage
            # ----------
            yield from self.set_response(epaddr_in, EndpointResponse.ACK)
            yield from self.transaction_data_in(addr, epaddr_in, descriptor_data)

            # Status stage
            # ----------
            yield from self.set_response(epaddr_out, EndpointResponse.NAK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, [])
            yield from self.expect_nak()

            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, [])
            yield from self.expect_nak()

            yield from self.set_response(epaddr_out, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr_out)
            yield from self.send_data_packet(PID.DATA1, [])
            yield from self.expect_ack()
            yield from self.expect_data(epaddr_out, [])
            yield from self.clear_pending(epaddr_out)

        self.run_sim(stim)

    def test_in_transfer(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(1, EndpointType.IN)

            d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8]

            yield from self.clear_pending(epaddr)
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield

            yield from self.set_data(epaddr, d[:4])
            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA1, d[:4])
            yield from self.send_ack()

            self.assertTrue((yield from self.pending(epaddr)))
            yield from self.set_data(epaddr, d[4:])
            yield from self.clear_pending(epaddr)

            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA0, d[4:])
            yield from self.send_ack()

        self.run_sim(stim)

    def test_data_in_byte_1(self):
        def stim():
            addr = 28

            ep1 = EndpointType.epaddr(1, EndpointType.IN)
            yield from self.clear_pending(ep1)
            yield from self.set_response(ep1, EndpointResponse.NAK)

            d1 = [0x1]
            yield from self.set_data(ep1, d1)
            yield from self.set_response(ep1, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep1)
            yield from self.expect_data_packet(PID.DATA1, d1)
            yield from self.send_ack()
            yield from self.clear_pending(ep1)

        self.run_sim(stim)

    def test_data_in_byte_2(self):
        def stim():
            addr = 28

            ep1 = EndpointType.epaddr(1, EndpointType.IN)
            yield from self.clear_pending(ep1)
            yield from self.set_response(ep1, EndpointResponse.NAK)

            d1 = [0x2]
            yield from self.set_data(ep1, d1)
            yield from self.set_response(ep1, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep1)
            yield from self.expect_data_packet(PID.DATA1, d1)
            yield from self.send_ack()
            yield from self.clear_pending(ep1)

        self.run_sim(stim)

    def test_data_in_byte_a(self):
        def stim():
            addr = 28

            ep1 = EndpointType.epaddr(1, EndpointType.IN)
            yield from self.clear_pending(ep1)
            yield from self.set_response(ep1, EndpointResponse.NAK)

            d1 = [0xa]
            yield from self.set_data(ep1, d1)
            yield from self.set_response(ep1, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep1)
            yield from self.expect_data_packet(PID.DATA1, d1)
            yield from self.send_ack()
            yield from self.clear_pending(ep1)

        self.run_sim(stim)

    def test_setup_clears_data_toggle_bit(self):
        def stim():
            addr = 28

            ep0in = EndpointType.epaddr(0, EndpointType.IN)
            yield from self.clear_pending(ep0in)
            yield from self.set_response(ep0in, EndpointResponse.NAK)

            ep0out = EndpointType.epaddr(0, EndpointType.OUT)
            yield from self.clear_pending(ep0out)
            yield from self.set_response(ep0out, EndpointResponse.NAK)
            yield

            # Setup stage
            yield from self.transaction_setup(28, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])

            dtbi = yield from self.dtb(ep0in)
            self.assertTrue(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertTrue(dtbo)

            # Data stage
            yield from self.set_response(ep0in, EndpointResponse.ACK)
            yield from self.transaction_data_in(addr, ep0in, [0x1])

            dtbi = yield from self.dtb(ep0in)
            self.assertFalse(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertTrue(dtbo)

            # Status stage
            yield from self.set_response(ep0out, EndpointResponse.ACK)
            yield from self.transaction_status_out(addr, ep0out)

            dtbi = yield from self.dtb(ep0in)
            self.assertFalse(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertFalse(dtbo)

            # Data transfer
            yield from self.set_response(ep0in, EndpointResponse.ACK)
            yield from self.transaction_data_in(addr, ep0in, [0x1], dtb=PID.DATA0)

            dtbi = yield from self.dtb(ep0in)
            self.assertTrue(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertFalse(dtbo)

            # Data transfer
            yield from self.set_response(ep0in, EndpointResponse.ACK)
            yield from self.transaction_data_in(addr, ep0in, [0x2], dtb=PID.DATA1)

            dtbi = yield from self.dtb(ep0in)
            self.assertFalse(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertFalse(dtbo)

            # New setup stage should reset dtb
            yield from self.transaction_setup(28, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])

            dtbi = yield from self.dtb(ep0in)
            self.assertTrue(dtbi)

            dtbo = yield from self.dtb(ep0out)
            self.assertTrue(dtbo)

        self.run_sim(stim)

    def test_data_toggle_bit_multiple_endpoints(self):
        def stim():
            addr = 28

            ep1 = EndpointType.epaddr(1, EndpointType.IN)
            yield from self.clear_pending(ep1)
            yield from self.set_response(ep1, EndpointResponse.NAK)
            ep2 = EndpointType.epaddr(2, EndpointType.IN)
            yield from self.clear_pending(ep2)
            yield from self.set_response(ep2, EndpointResponse.NAK)
            yield

            d1 = [0x1]
            yield from self.set_data(ep1, d1)
            yield from self.set_response(ep1, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep1)
            yield from self.expect_data_packet(PID.DATA1, d1)
            yield from self.send_ack()
            yield from self.clear_pending(ep1)

            d2 = [0x2]
            yield from self.set_data(ep2, d2)
            yield from self.set_response(ep2, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep2)
            yield from self.expect_data_packet(PID.DATA1, d2)
            yield from self.send_ack()
            yield from self.clear_pending(ep2)

            d3 = [0x3]
            yield from self.set_data(ep2, d3)
            yield from self.set_response(ep2, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep2)
            yield from self.expect_data_packet(PID.DATA0, d3)
            yield from self.send_ack()
            yield from self.clear_pending(ep2)

            d4 = [0x5]
            yield from self.set_data(ep1, d4)
            yield from self.set_response(ep1, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, ep1)
            yield from self.expect_data_packet(PID.DATA0, d4)
            yield from self.send_ack()
            yield from self.clear_pending(ep1)

        self.run_sim(stim)

    def test_in_transfer_nak(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(1, EndpointType.IN)

            yield from self.clear_pending(epaddr)
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield

            # Device NAK the PID.IN token packet
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_nak()

            # Device NAK the PID.IN token packet a second time
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_nak()

            d1 = [0x1, 0x2, 0x3, 0x4]
            yield from self.set_data(epaddr, d1)
            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA1, d1)
            yield from self.send_ack()
            yield from self.clear_pending(epaddr)

            # Have data but was asked to NAK
            d2 = [0x5, 0x6, 0x7, 0x8]
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield from self.set_data(epaddr, d2)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_nak()

            # Actually send the data now
            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA0, d2)
            yield from self.send_ack()
            yield from self.clear_pending(epaddr)

        self.run_sim(stim)

    def test_in_stall(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(1, EndpointType.IN)

            d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8]

            # While pending, set stall
            self.assertTrue((yield from self.pending(epaddr)))
            yield from self.set_response(epaddr, EndpointResponse.STALL)
            yield from self.set_data(epaddr, d[:4])
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_stall()

            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_nak()
            yield from self.clear_pending(epaddr)

            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA1, d[:4])
            yield from self.send_ack()
            yield from self.set_data(epaddr, d[4:])
            yield from self.clear_pending(epaddr)

            # While not pending, set stall
            self.assertFalse((yield from self.pending(epaddr)))
            yield from self.set_response(epaddr, EndpointResponse.STALL)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_stall()

            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.IN, addr, epaddr)
            yield from self.expect_data_packet(PID.DATA0, d[4:])
            yield from self.send_ack()
            yield from self.clear_pending(epaddr)

        self.run_sim(stim)

    def test_out_transfer(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(2, EndpointType.OUT)

            d = [0x41, 0x01]

            yield from self.clear_pending(epaddr)
            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield

            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, d)

            # Should nak until pending is cleared
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_nak()

            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_nak()

            # Make sure no extra data turned up
            #yield from self.expect_data(epaddr, [])

            yield from self.clear_pending(epaddr)

            d2 = [0x41, 0x02]
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d2)
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, d2)
            yield from self.clear_pending(epaddr)

        self.run_sim(stim)

    def test_out_transfer_nak(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(2, EndpointType.OUT)

            d = [0x41, 0x01]

            yield from self.clear_pending(epaddr)
            yield from self.set_response(epaddr, EndpointResponse.NAK)
            yield

            # First nak
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_nak()
            pending = yield from self.pending(epaddr)
            self.assertFalse(pending)

            # Second nak
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_nak()
            pending = yield from self.pending(epaddr)
            self.assertFalse(pending)

            # Third attempt succeeds
            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d)
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, d)
            yield from self.clear_pending(epaddr)

        self.run_sim(stim)

    def test_out_stall(self):
        def stim():
            addr = 28
            epaddr = EndpointType.epaddr(2, EndpointType.OUT)

            d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8]

            # While pending, set stall
            self.assertTrue((yield from self.pending(epaddr)))
            yield from self.set_response(epaddr, EndpointResponse.STALL)
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d[:4])
            yield from self.expect_stall()

            yield from self.set_response(epaddr, EndpointResponse.ACK)
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d[:4])
            yield from self.expect_nak()
            yield from self.clear_pending(epaddr)

            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA1, d[:4])
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, d[:4])
            yield from self.clear_pending(epaddr)

            # While not pending, set stall
            self.assertFalse((yield from self.pending(epaddr)))
            yield from self.set_response(epaddr, EndpointResponse.STALL)
            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA0, d[4:])
            yield from self.expect_stall()

            yield from self.set_response(epaddr, EndpointResponse.ACK)

            yield from self.send_token_packet(PID.OUT, addr, epaddr)
            yield from self.send_data_packet(PID.DATA0, d[4:])
            yield from self.expect_ack()
            yield from self.expect_data(epaddr, d[4:])
            yield from self.clear_pending(epaddr)

        self.run_sim(stim)
