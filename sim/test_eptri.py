# Simple tests for an adder module
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from cocotb.result import TestFailure, TestSuccess, ReturnValue

from valentyusb.usbcore.utils.packet import *
from valentyusb.usbcore.endpoint import *
from valentyusb.usbcore.pid import *
from valentyusb.usbcore.utils.pprint import pp_packet

from wishbone import WishboneMaster, WBOp

import logging
import csv

class UsbTest:
    def __init__(self, dut):
        self.dut = dut
        self.csrs = dict()
        with open("csr.csv", newline='') as csr_csv_file:
            csr_csv = csv.reader(csr_csv_file)
            # csr_register format: csr_register, name, address, size, rw/ro
            for row in csr_csv:
                if row[0] == 'csr_register':
                    self.csrs[row[1]] = int(row[2], base=0)
        cocotb.fork(Clock(dut.clk48, int(20.83), 'ns').start())
        self.wb = WishboneMaster(dut, "wishbone", dut.clk12, timeout=20)

    @cocotb.coroutine
    def write(self, addr, val):
        yield self.wb.write(addr, val)

    @cocotb.coroutine
    def read(self, addr):
        value = yield self.wb.read(addr)
        raise ReturnValue(value)

    @cocotb.coroutine
    def connect(self):
        USB_PULLUP_OUT = self.csrs['usb_pullup_out']
        yield self.write(USB_PULLUP_OUT, 1)

    def assertEqual(self, a, b, msg):
        if a != b:
            raise TestFailure(msg)

    def assertSequenceEqual(self, a, b, msg):
        if a != b:
            raise TestFailure(msg)

    def print_ep(self, epaddr, msg, *args):
        self.dut._log.info("ep(%i, %s): %s" % (
            EndpointType.epnum(epaddr),
            EndpointType.epdir(epaddr).name,
            msg) % args)

    # Host->Device
    @cocotb.coroutine
    def _host_send_packet(self, packet):
        """Send a USB packet."""

        # Packet gets multiplied by 4x so we can send using the
        # usb48 clock instead of the usb12 clock.
        packet = wrap_packet(packet)
        self.assertEqual('J', packet[-1], "Packet didn't end in J: "+packet)

        for v in packet:
            if v == '0' or v == '_':
                # SE0 - both lines pulled low
                self.dut.usb_d_p <= 0
                self.dut.usb_d_n <= 0
            elif v == '1':
                # SE1 - illegal, should never occur
                self.dut.usb_d_p <= 1
                self.dut.usb_d_n <= 1
            elif v == '-' or v == 'I':
                # Idle
                self.dut.usb_d_p <= 1
                self.dut.usb_d_n <= 0
            elif v == 'J':
                self.dut.usb_d_p <= 1
                self.dut.usb_d_n <= 0
            elif v == 'K':
                self.dut.usb_d_p <= 0
                self.dut.usb_d_n <= 1
            else:
                raise TestFailure("Unknown value: %s" % v)
            yield RisingEdge(self.dut.clk48)

    @cocotb.coroutine
    def host_send_token_packet(self, pid, addr, epnum):
        yield self._host_send_packet(token_packet(pid, addr, epnum))

    @cocotb.coroutine
    def host_send_data_packet(self, pid, data):
        assert pid in (PID.DATA0, PID.DATA1), pid
        yield self._host_send_packet(data_packet(pid, data))

    @cocotb.coroutine
    def host_send_ack(self):
        yield self._host_send_packet(handshake_packet(PID.ACK))

    @cocotb.coroutine
    def host_send(self, token, data01, addr, epnum, data):
        """Send data out the virtual USB connection, including an OUT token"""
        yield self.host_send_token_packet(token, addr, epnum)
        yield self.host_send_data_packet(data01, data)
        yield self.host_expect_ack()

    @cocotb.coroutine
    def host_recv(self, token, data01, addr, epnum, data):
        """Send data out the virtual USB connection, including an OUT token"""
        yield self.host_send_token_packet(token, addr, epnum)
        yield self.host_expect_data_packet(data01, data)
        yield self.host_send_ack()

    # Device->Host
    @cocotb.coroutine
    def host_expect_packet(self, packet, msg=None):
        """Except to receive the following USB packet."""

        def current():
            values = (self.dut.usb_d_p, self.dut.usb_d_n)

            if values == (0, 0):
                return '_'
            elif values == (1, 1):
                return '1'
            elif values == (1, 0):
                return 'J'
            elif values == (0, 1):
                return 'K'
            else:
                raise TestFailure("Unrecognized dut values: {}".format(values))

        # Wait for transmission to start
        tx = 0
        bit_times = 0
        for i in range(0, 100):
            tx = self.dut.usb_tx_en
            if tx == 1:
                break
            yield RisingEdge(self.dut.clk48)
            bit_times = bit_times + 1
        if tx != 1:
            raise TestFailure("No packet started, " + msg)

        # # USB specifies that the turn-around time is 7.5 bit times for the device
        bit_time_max = 12.5
        bit_time_acceptable = 7.5
        if (bit_times/4.0) > bit_time_max:
            raise TestFailure("Response came after {} bit times, which is more than {}".format(bit_times / 4.0, bit_time_max))
        if (bit_times/4.0) > bit_time_acceptable:
            self.dut._log.warn("Response came after {} bit times (> {})".format(bit_times / 4.0, bit_time_acceptable))
        else:
            self.dut._log.info("Response came after {} bit times".format(bit_times / 4.0))

        # Read in the transmission data
        result = ""
        for i in range(0, 512):
            result += current()
            yield RisingEdge(self.dut.clk48)
            if self.dut.usb_tx_en != 1:
                break
        if tx == 1:
            raise TestFailure("Packet didn't finish, " + msg)

        # Check the packet received matches
        expected = pp_packet(wrap_packet(packet))
        actual = pp_packet(result)
        self.assertSequenceEqual(expected, actual, msg)

    @cocotb.coroutine
    def host_expect_ack(self):
        yield self.host_expect_packet(handshake_packet(PID.ACK), "Expected ACK packet.")

    @cocotb.coroutine
    def host_expect_data_packet(self, pid, data):
        assert pid in (PID.DATA0, PID.DATA1), pid
        yield self.host_expect_packet(data_packet(pid, data), "Expected %s packet with %r" % (pid.name, data))

    @cocotb.coroutine
    def expect_setup(self, epaddr, expected_data):
        actual_data = []
        # wait for data to appear
        for i in range(128):
            self.dut._log.debug("Prime loop {}".format(i))
            status = yield self.read(self.csrs['usb_setup_status'])
            have = status & 1
            if have:
                break
            yield RisingEdge(self.dut.clk12)

        for i in range(48):
            self.dut._log.debug("Read loop {}".format(i))
            status = yield self.read(self.csrs['usb_setup_status'])
            have = status & 1
            if not have:
                break
            v = yield self.read(self.csrs['usb_setup_data'])
            yield self.write(self.csrs['usb_setup_ctrl'], 1)
            actual_data.append(v)
            yield RisingEdge(self.dut.clk12)

        if len(actual_data) < 2:
            raise TestFailure("data {} was short".format(actual_data))
        actual_data, actual_crc16 = actual_data[:-2], actual_data[-2:]

        self.print_ep(epaddr, "Got: %r (expected: %r)", actual_data, expected_data)
        self.assertSequenceEqual(expected_data, actual_data, "SETUP packet not received")
        self.assertSequenceEqual(crc16(expected_data), actual_crc16, "CRC16 not valid")

    @cocotb.coroutine
    def set_response(self, ep, response):
        raise ReturnValue(None)

    @cocotb.coroutine
    def send_data(self, token, ep, data):
        for b in data:
            yield self.write(self.csrs['usb_epin_data'], b)
        yield self.write(self.csrs['usb_epin_epno'], ep)

    @cocotb.coroutine
    def transaction_setup(self, addr, data, epnum=0):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

        xmit = cocotb.fork(self.host_send(PID.SETUP, PID.DATA0, addr, epnum, data))
        yield self.expect_setup(epaddr_out, data)
        yield xmit.join()

    @cocotb.coroutine
    def transaction_data_out(self, addr, ep, data, chunk_size=8):
        epnum = EndpointType.epnum(ep)
        datax = PID.DATA0
        for i, chunk in enumerate(grouper(chunk_size, data, pad=0)):
            recv = cocotb.fork(self.host_recv(PID.OUT, datax, addr, epnum, chunk))
            yield self.send_data(datax, epnum, data)
            yield recv.join()

            if datax == PID.DATA0:
                datax = PID.DATA1
            else:
                datax = PID.DATA0

    @cocotb.coroutine
    def transaction_status_in(self, addr, ep):
        epnum = EndpointType.epnum(ep)
        assert EndpointType.epdir(ep) == EndpointType.IN
        xmit = cocotb.fork(self.host_send(PID.IN, PID.DATA1, addr, epnum, []))
        yield xmit.join()

    @cocotb.coroutine
    def control_transfer_out(self, addr, setup_data, descriptor_data):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
        # Setup stage
        yield self.transaction_setup(addr, setup_data)

        # Data stage
        yield self.set_response(epaddr_out, EndpointResponse.ACK)
        yield self.transaction_data_out(addr, epaddr_out, descriptor_data)

        # Status stage
        yield self.set_response(epaddr_in, EndpointResponse.ACK)
        yield self.transaction_status_in(addr, epaddr_in)

@cocotb.test()
def iobuf_validate(dut):
    """Sanity test that the Wishbone bus actually works"""
    harness = UsbTest(dut)
    # harness.wb.log.setLevel(logging.DEBUG)

    USB_PULLUP_OUT = harness.csrs['usb_pullup_out']
    val = yield harness.read(USB_PULLUP_OUT)
    dut._log.info("Value at start: {}".format(val))
    if dut.usb_pullup != 0:
        raise TestFailure("USB pullup is not zero")

    yield harness.write(USB_PULLUP_OUT, 1)

    val = yield harness.read(USB_PULLUP_OUT)
    dut._log.info("Memory value: {}".format(val))
    if val != 1:
        raise TestFailure("USB pullup is not set!")
    raise TestSuccess("iobuf validated")

@cocotb.test()
def test_control_setup(dut):
    harness = UsbTest(dut)
    yield harness.connect()
    #   012345   0123
    # 0b011100 0b1000
    yield harness.transaction_setup(28, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])

@cocotb.test()
def test_control_transfer_out(dut):
    harness = UsbTest(dut)
    yield harness.connect()

    yield harness.control_transfer_out(
        20,
        # Get descriptor, Index 0, Type 03, LangId 0000, wLength 10?
        [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00],
        # 12 byte descriptor, max packet size 8 bytes
        [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
            0x08, 0x09, 0x0A, 0x0B],
    )