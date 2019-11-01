# Tests for the Fomu Tri-Endpoint
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, NullTrigger, Timer
from cocotb.result import TestFailure, TestSuccess, ReturnValue

from valentyusb.usbcore.utils.packet import *
from valentyusb.usbcore.endpoint import *
from valentyusb.usbcore.pid import *
from valentyusb.usbcore.utils.pprint import pp_packet

from wishbone import WishboneMaster, WBOp

import logging
import csv

def grouper_tofit(n, iterable):
    from itertools import zip_longest
    """Group iterable into multiples of n, except don't leave
    trailing None values at the end.
    """
    # itertools.zip_longest is broken because it requires you to fill in some
    # value, and doesn't mention anything else in its documentation that would
    # not require this behavior.
    # Re-do the array to shrink it down if any None values are discovered.
    broken = zip_longest(*[iter(iterable)]*n, fillvalue=None)
    fixed = []
    for e in broken:
        f = []
        for el in e:
            if el is not None:
                f.append(el)
        fixed.append(f)
    return fixed

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
        cocotb.fork(Clock(dut.clk48, 20800, 'ps').start())
        self.wb = WishboneMaster(dut, "wishbone", dut.clk12, timeout=20)

        # Set the signal "test_name" to match this test
        import inspect
        tn = cocotb.binary.BinaryValue(value=None, n_bits=4096)
        tn.buff = inspect.stack()[1][3]
        self.dut.test_name = tn

    @cocotb.coroutine
    def reset(self):

        self.dut.reset = 1
        yield RisingEdge(self.dut.clk12)
        self.dut.reset = 0
        yield RisingEdge(self.dut.clk12)

        self.dut.usb_d_p = 1
        self.dut.usb_d_n = 0

        yield self.disconnect()

        # Enable endpoint 0
        yield self.write(self.csrs['usb_setup_ev_enable'], 0xff)
        yield self.write(self.csrs['usb_in_ev_enable'], 0xff)
        yield self.write(self.csrs['usb_out_ev_enable'], 0xff)

        yield self.write(self.csrs['usb_setup_ev_pending'], 0xff)
        yield self.write(self.csrs['usb_in_ev_pending'], 0xff)
        yield self.write(self.csrs['usb_out_ev_pending'], 0xff)
        yield self.write(self.csrs['usb_address'], 0)

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

    @cocotb.coroutine
    def clear_pending(self, epaddr):
        if EndpointType.epdir(epaddr) == EndpointType.IN:
            # Reset endpoint
            self.dut._log.info("Clearing IN_EV_PENDING")
            yield self.write(self.csrs['usb_in_ctrl'], 0x20)
            yield self.write(self.csrs['usb_in_ev_pending'], 0xff)
        else:
            self.dut._log.info("Clearing OUT_EV_PENDING")
            yield self.write(self.csrs['usb_out_ev_pending'], 0xff)
            yield self.write(self.csrs['usb_out_ctrl'], 0x20)

    @cocotb.coroutine
    def disconnect(self):
        USB_PULLUP_OUT = self.csrs['usb_pullup_out']
        yield self.write(USB_PULLUP_OUT, 0)

    def assertEqual(self, a, b, msg):
        if a != b:
            raise TestFailure("{} != {} - {}".format(a, b, msg))

    def assertSequenceEqual(self, a, b, msg):
        if a != b:
            raise TestFailure("{} vs {} - {}".format(a, b, msg))

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
        packet = 'JJJJJJJJ' + wrap_packet(packet)
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
    def host_send_token_packet(self, pid, addr, ep):
        epnum = EndpointType.epnum(ep)
        yield self._host_send_packet(token_packet(pid, addr, epnum))

    @cocotb.coroutine
    def host_send_data_packet(self, pid, data):
        assert pid in (PID.DATA0, PID.DATA1), pid
        yield self._host_send_packet(data_packet(pid, data))

    @cocotb.coroutine
    def host_send_sof(self, time):
        yield self._host_send_packet(sof_packet(time))

    @cocotb.coroutine
    def host_send_ack(self):
        yield self._host_send_packet(handshake_packet(PID.ACK))

    @cocotb.coroutine
    def host_send(self, data01, addr, epnum, data, expected=PID.ACK):
        """Send data out the virtual USB connection, including an OUT token"""
        yield self.host_send_token_packet(PID.OUT, addr, epnum)
        yield self.host_send_data_packet(data01, data)
        yield self.host_expect_packet(handshake_packet(expected), "Expected {} packet.".format(expected))


    @cocotb.coroutine
    def host_setup(self, addr, epnum, data):
        """Send data out the virtual USB connection, including a SETUP token"""
        yield self.host_send_token_packet(PID.SETUP, addr, epnum)
        yield self.host_send_data_packet(PID.DATA0, data)
        yield self.host_expect_ack()

    @cocotb.coroutine
    def host_recv(self, data01, addr, epnum, data):
        """Send data out the virtual USB connection, including an IN token"""
        yield self.host_send_token_packet(PID.IN, addr, epnum)
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
        for i in range(0, 4096):
            result += current()
            yield RisingEdge(self.dut.clk48)
            if self.dut.usb_tx_en != 1:
                break
        if tx == 1:
            raise TestFailure("Packet didn't finish, " + msg)
        self.dut.usb_d_p = 1
        self.dut.usb_d_n = 0

        # Check the packet received matches
        expected = pp_packet(wrap_packet(packet))
        actual = pp_packet(result)
        self.assertSequenceEqual(expected, actual, msg)

    @cocotb.coroutine
    def host_expect_ack(self):
        yield self.host_expect_packet(handshake_packet(PID.ACK), "Expected ACK packet.")

    @cocotb.coroutine
    def host_expect_nak(self):
        yield self.host_expect_packet(handshake_packet(PID.NAK), "Expected NAK packet.")

    @cocotb.coroutine
    def host_expect_stall(self):
        yield self.host_expect_packet(handshake_packet(PID.STALL), "Expected STALL packet.")

    @cocotb.coroutine
    def host_expect_data_packet(self, pid, data):
        assert pid in (PID.DATA0, PID.DATA1), pid
        yield self.host_expect_packet(data_packet(pid, data), "Expected %s packet with %r" % (pid.name, data))

    @cocotb.coroutine
    def pending(self, ep):
        if EndpointType.epdir(ep) == EndpointType.IN:
            val = yield self.read(self.csrs['usb_in_status'])
            raise ReturnValue(val & (1 << 4))
        else:
            val = yield self.read(self.csrs['usb_out_status'])
            raise ReturnValue((val & (1 << 5) | (1 << 4)) and (EndpointType.epnum(ep) == (val & 0x0f)))

    @cocotb.coroutine
    def expect_setup(self, epaddr, expected_data):
        actual_data = []
        # wait for data to appear
        for i in range(128):
            self.dut._log.debug("Prime loop {}".format(i))
            status = yield self.read(self.csrs['usb_setup_status'])
            have = status & 0x10
            if have:
                break
            yield RisingEdge(self.dut.clk12)

        for i in range(48):
            self.dut._log.debug("Read loop {}".format(i))
            status = yield self.read(self.csrs['usb_setup_status'])
            have = status & 0x10
            if not have:
                break
            v = yield self.read(self.csrs['usb_setup_data'])
            actual_data.append(v)
            yield RisingEdge(self.dut.clk12)

        if len(actual_data) < 2:
            raise TestFailure("data was short (got {}, expected {})".format(expected_data, actual_data))
        actual_data, actual_crc16 = actual_data[:-2], actual_data[-2:]

        self.print_ep(epaddr, "Got: %r (expected: %r)", actual_data, expected_data)
        self.assertSequenceEqual(expected_data, actual_data, "SETUP packet not received")
        self.assertSequenceEqual(crc16(expected_data), actual_crc16, "CRC16 not valid")

        # Acknowledge that we've handled the setup packet
        yield self.write(self.csrs['usb_setup_ctrl'], 2)

    @cocotb.coroutine
    def drain_setup(self):
        actual_data = []
        for i in range(48):
            status = yield self.read(self.csrs['usb_setup_status'])
            have = status & 0x10
            if not have:
                break
            v = yield self.read(self.csrs['usb_setup_data'])
            actual_data.append(v)
            yield RisingEdge(self.dut.clk12)
        yield self.write(self.csrs['usb_setup_ctrl'], 2)
        # Drain the pending bit
        yield self.write(self.csrs['usb_setup_ev_pending'], 0xff)
        return actual_data

    @cocotb.coroutine
    def drain_out(self):
        actual_data = []
        for i in range(70):
            status = yield self.read(self.csrs['usb_out_status'])
            have = status & (1 << 4)
            if not have:
                break
            v = yield self.read(self.csrs['usb_out_data'])
            actual_data.append(v)
            yield RisingEdge(self.dut.clk12)
        yield self.write(self.csrs['usb_out_ev_pending'], 0xff)
        yield self.write(self.csrs['usb_out_ctrl'], 0x10)
        return actual_data[:-2] # Strip off CRC16

    @cocotb.coroutine
    def expect_data(self, epaddr, expected_data, expected):
        actual_data = []
        # wait for data to appear
        for i in range(128):
            self.dut._log.debug("Prime loop {}".format(i))
            status = yield self.read(self.csrs['usb_out_status'])
            have = status & (1 << 4)
            if have:
                break
            yield RisingEdge(self.dut.clk12)

        for i in range(256):
            self.dut._log.debug("Read loop {}".format(i))
            status = yield self.read(self.csrs['usb_out_status'])
            have = status & (1 << 4)
            if not have:
                break
            v = yield self.read(self.csrs['usb_out_data'])
            actual_data.append(v)
            yield RisingEdge(self.dut.clk12)

        if expected == PID.ACK:
            if len(actual_data) < 2:
                raise TestFailure("data {} was short".format(actual_data))
            actual_data, actual_crc16 = actual_data[:-2], actual_data[-2:]

            self.print_ep(epaddr, "Got: %r (expected: %r)", actual_data, expected_data)
            self.assertSequenceEqual(expected_data, actual_data, "DATA packet not correctly received")
            self.assertSequenceEqual(crc16(expected_data), actual_crc16, "CRC16 not valid")
            pending = yield self.read(self.csrs['usb_out_ev_pending'])
            if pending != 1:
                raise TestFailure('event not generated')
            yield self.write(self.csrs['usb_out_ev_pending'], pending)

    @cocotb.coroutine
    def set_response(self, ep, response):
        if EndpointType.epdir(ep) == EndpointType.IN and response == EndpointResponse.ACK:
            yield self.write(self.csrs['usb_in_ctrl'], EndpointType.epnum(ep))
        elif EndpointType.epdir(ep) == EndpointType.OUT and response == EndpointResponse.ACK:
            yield self.write(self.csrs['usb_out_ctrl'], 0x10 | EndpointType.epnum(ep))

    @cocotb.coroutine
    def send_data(self, token, ep, data):
        for b in data:
            yield self.write(self.csrs['usb_in_data'], b)
        yield self.write(self.csrs['usb_in_ctrl'], EndpointType.epnum(ep) & 0x0f)

    @cocotb.coroutine
    def transaction_setup(self, addr, data, epnum=0):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

        xmit = cocotb.fork(self.host_setup(addr, epnum, data))
        yield xmit.join()
        yield self.expect_setup(epaddr_out, data)

    @cocotb.coroutine
    def transaction_data_out(self, addr, ep, data, chunk_size=64, expected=PID.ACK, datax=PID.DATA1):
        epnum = EndpointType.epnum(ep)

        for _i, chunk in enumerate(grouper_tofit(chunk_size, data)):
            self.dut._log.warning("sending {} bytes to host on endpoint {}".format(len(chunk), epnum))
            # Enable receiving data
            yield self.set_response(ep, EndpointResponse.ACK)
            xmit = cocotb.fork(self.host_send(datax, addr, ep, chunk, expected))
            yield self.expect_data(epnum, list(chunk), expected)
            yield xmit.join()

            if datax == PID.DATA0:
                datax = PID.DATA1
            else:
                datax = PID.DATA0

    @cocotb.coroutine
    def transaction_data_in(self, addr, ep, data, chunk_size=64, datax=PID.DATA1):
        epnum = EndpointType.epnum(ep)
        sent_data = 0
        for i, chunk in enumerate(grouper_tofit(chunk_size, data)):
            sent_data = 1
            self.dut._log.debug("Actual data we're expecting: {}".format(chunk))
            for b in chunk:
                yield self.write(self.csrs['usb_in_data'], b)
            yield self.write(self.csrs['usb_in_ctrl'], epnum)
            recv = cocotb.fork(self.host_recv(datax, addr, ep, chunk))
            yield recv.join()

            if datax == PID.DATA0:
                datax = PID.DATA1
            else:
                datax = PID.DATA0
        if not sent_data:
            yield self.write(self.csrs['usb_in_ctrl'], epnum)
            recv = cocotb.fork(self.host_recv(datax, addr, ep, []))
            yield self.send_data(datax, epnum, data)
            yield recv.join()

    @cocotb.coroutine
    def set_data(self, ep, data):
        _epnum = EndpointType.epnum(ep)
        for b in data:
            yield self.write(self.csrs['usb_in_data'], b)

    @cocotb.coroutine
    def transaction_status_in(self, addr, ep):
        epnum = EndpointType.epnum(ep)
        assert EndpointType.epdir(ep) == EndpointType.IN
        xmit = cocotb.fork(self.host_recv(PID.DATA1, addr, ep, []))
        yield xmit.join()

    @cocotb.coroutine
    def transaction_status_out(self, addr, ep):
        epnum = EndpointType.epnum(ep)
        assert EndpointType.epdir(ep) == EndpointType.OUT
        xmit = cocotb.fork(self.host_send(PID.DATA1, addr, ep, []))
        yield xmit.join()

    @cocotb.coroutine
    def control_transfer_out(self, addr, setup_data, descriptor_data=None):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

        if (setup_data[0] & 0x80) == 0x80:
            raise Exception("setup_data indicated an IN transfer, but you requested an OUT transfer")

        setup_ev = yield self.read(self.csrs['usb_setup_ev_pending'])
        if setup_ev != 0:
            raise TestFailure("setup_ev should be 0 at the start of the test, was: {:02x}".format(setup_ev))

        # Setup stage
        self.dut._log.info("setup stage")
        yield self.transaction_setup(addr, setup_data)

        setup_ev = yield self.read(self.csrs['usb_setup_ev_pending'])
        if setup_ev != 1:
            raise TestFailure("setup_ev should be 1, was: {:02x}".format(setup_ev))
        yield self.write(self.csrs['usb_setup_ev_pending'], setup_ev)

        # Data stage
        if descriptor_data is not None:
            out_ev = yield self.read(self.csrs['usb_out_ev_pending'])
            if out_ev != 0:
                raise TestFailure("out_ev should be 0 at the start of the test, was: {:02x}".format(out_ev))
        if (setup_data[7] != 0 or setup_data[6] != 0) and descriptor_data is None:
            raise Exception("setup_data indicates data, but no descriptor data was specified")
        if (setup_data[7] == 0 and setup_data[6] == 0) and descriptor_data is not None:
            raise Exception("setup_data indicates no data, but descriptor data was specified")
        if descriptor_data is not None:
            yield self.host_send_token_packet(PID.OUT, 0, 0)
            yield self.host_send_data_packet(PID.DATA1, descriptor_data[:64])
            yield self.host_expect_nak()
            self.dut._log.info("data stage")
            yield self.transaction_data_out(addr, epaddr_out, descriptor_data)

        # Status stage
        self.dut._log.info("status stage")
        yield self.write(self.csrs['usb_in_ctrl'], 0) # Send an empty IN packet
        in_ev = yield self.read(self.csrs['usb_in_ev_pending'])
        if in_ev != 0:
            raise TestFailure("o: in_ev should be 0 at the start of the test, was: {:02x}".format(in_ev))
        yield self.transaction_status_in(addr, epaddr_in)
        yield RisingEdge(self.dut.clk12)
        yield RisingEdge(self.dut.clk12)
        in_ev = yield self.read(self.csrs['usb_in_ev_pending'])
        if in_ev != 1:
            raise TestFailure("o: in_ev should be 1 at the end of the test, was: {:02x}".format(in_ev))
        yield self.write(self.csrs['usb_in_ev_pending'], in_ev)
        yield self.write(self.csrs['usb_in_ctrl'], 1 << 5) # Reset the IN buffer

    @cocotb.coroutine
    def control_transfer_in(self, addr, setup_data, descriptor_data=None):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

        if (setup_data[0] & 0x80) == 0x00:
            raise Exception("setup_data indicated an OUT transfer, but you requested an IN transfer")

        setup_ev = yield self.read(self.csrs['usb_setup_ev_pending'])
        if setup_ev != 0:
            raise TestFailure("setup_ev should be 0 at the start of the test, was: {:02x}".format(setup_ev))

        # Setup stage
        self.dut._log.info("setup stage")
        yield self.transaction_setup(addr, setup_data)

        setup_ev = yield self.read(self.csrs['usb_setup_ev_pending'])
        if setup_ev != 1:
            raise TestFailure("setup_ev should be 1, was: {:02x}".format(setup_ev))
        yield self.write(self.csrs['usb_setup_ev_pending'], setup_ev)

        # Data stage
        in_ev = yield self.read(self.csrs['usb_in_ev_pending'])
        if in_ev != 0:
            raise TestFailure("in_ev should be 0 at the start of the test, was: {:02x}".format(in_ev))
        if (setup_data[7] != 0 or setup_data[6] != 0) and descriptor_data is None:
            raise Exception("setup_data indicates data, but no descriptor data was specified")
        if (setup_data[7] == 0 and setup_data[6] == 0) and descriptor_data is not None:
            raise Exception("setup_data indicates no data, but descriptor data was specified")
        if descriptor_data is not None:
            self.dut._log.info("data stage")
            yield self.transaction_data_in(addr, epaddr_in, descriptor_data)

            # Give the signal two clock cycles to percolate through the event manager
            yield RisingEdge(self.dut.clk12)
            yield RisingEdge(self.dut.clk12)
            in_ev = yield self.read(self.csrs['usb_in_ev_pending'])
            if in_ev != 1:
                raise TestFailure("in_ev should be 1 at the end of the test, was: {:02x}".format(in_ev))
            yield self.write(self.csrs['usb_in_ev_pending'], in_ev)

        # Status stage
        yield self.write(self.csrs['usb_out_ctrl'], 0x10) # Send empty packet
        self.dut._log.info("status stage")
        out_ev = yield self.read(self.csrs['usb_out_ev_pending'])
        if out_ev != 0:
            raise TestFailure("i: out_ev should be 0 at the start of the test, was: {:02x}".format(out_ev))
        yield self.transaction_status_out(addr, epaddr_out)
        yield RisingEdge(self.dut.clk12)
        out_ev = yield self.read(self.csrs['usb_out_ev_pending'])
        if out_ev != 1:
            raise TestFailure("i: out_ev should be 1 at the end of the test, was: {:02x}".format(out_ev))
        yield self.write(self.csrs['usb_out_ctrl'], 0x20) # Reset FIFO
        yield self.write(self.csrs['usb_out_ev_pending'], out_ev)

@cocotb.test()
def iobuf_validate(dut):
    """Sanity test that the Wishbone bus actually works"""
    harness = UsbTest(dut)
    yield harness.reset()

    USB_PULLUP_OUT = harness.csrs['usb_pullup_out']
    val = yield harness.read(USB_PULLUP_OUT)
    dut._log.info("Value at start: {}".format(val))
    if dut.usb_pullup != 0:
        raise TestFailure("USB pullup didn't start at zero")

    yield harness.write(USB_PULLUP_OUT, 1)

    val = yield harness.read(USB_PULLUP_OUT)
    dut._log.info("Memory value: {}".format(val))
    if val != 1:
        raise TestFailure("USB pullup is not set!")
    raise TestSuccess("iobuf validated")

@cocotb.test()
def test_control_setup(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()
    # We write to address 0, because we just want to test that the control
    # circuitry works.  Normally you wouldn't do this.
    yield harness.write(harness.csrs['usb_address'], 0)
    yield harness.transaction_setup(0, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x00, 0x00])
    yield harness.transaction_data_in(0, 0, [])

@cocotb.test()
def test_control_transfer_in(dut):
    harness = UsbTest(dut)
    yield harness.reset()

    yield harness.connect()
    yield harness.write(harness.csrs['usb_address'], 20)
    yield harness.control_transfer_in(
        20,
        # Get descriptor, Index 0, Type 03, LangId 0000, wLength 10?
        [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00],
        # 12 byte descriptor, max packet size 8 bytes
        [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
            0x08, 0x09, 0x0A, 0x0B],
    )

@cocotb.test()
def test_control_transfer_in_data_out(dut):
    harness = UsbTest(dut)
    ep3_out = EndpointType.epaddr(3, EndpointType.OUT)
    ep3_in = EndpointType.epaddr(3, EndpointType.IN)
    yield harness.reset()

    yield harness.connect()
    yield harness.write(harness.csrs['usb_address'], 0)
    yield harness.control_transfer_in(
        0,
        # Get descriptor, Index 0, Type 03, LangId 0000, wLength 10?
        [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00],
        # 12 byte descriptor, max packet size 8 bytes
        [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
            0x08, 0x09, 0x0A, 0x0B],
    )

    yield harness.transaction_data_out(0, ep3_out, [0, 0], chunk_size=64, expected=PID.ACK, datax=PID.DATA0)
    yield harness.transaction_data_in(0, ep3_in, [0, 0, 0, 0, 0, 0, 0, 0], chunk_size=64)


@cocotb.test()
def test_control_transfer_in_lazy(dut):
    """Test that we can transfer data in without immediately draining it"""
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

    harness = UsbTest(dut)
    yield harness.reset()

    yield harness.connect()
    yield harness.write(harness.csrs['usb_address'], 0)
### SETUP packet
    harness.dut._log.info("sending initial SETUP packet")
    # Send a SETUP packet without draining it on the device side
    yield harness.host_send_token_packet(PID.SETUP, 0, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x80, 0x06, 0x00, 0x05, 0x00, 0x00, 0x0A, 0x00])
    yield harness.host_expect_ack()

    # Set it up so we ACK the final IN packet
    data = [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
            0x08, 0x09, 0x0A, 0x0B]
    for b in data:
        yield harness.write(harness.csrs['usb_in_data'], b)

    # Send a few packets while we "process" the data as a slow host
    for i in range(2):
        yield harness.host_send_token_packet(PID.IN, 0, 0)
        yield harness.host_expect_nak()

    # Queue the IN response packet
    yield harness.write(harness.csrs['usb_in_ctrl'], 0)

    # Read the data
    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("1. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), setup_data))

    # Perform the final "read"
    yield harness.host_recv(PID.DATA1, 0, 0, data)

    # Status stage
    yield harness.set_response(epaddr_out, EndpointResponse.ACK)
    yield harness.transaction_status_out(0, epaddr_out)

### SET ADDRESS
    harness.dut._log.info("setting USB address")
    # Set the address.  Again, don't drain the device side yet.
    yield harness.host_send_token_packet(PID.SETUP, 0, epaddr_out)
    yield harness.host_send_data_packet(PID.DATA0, [0x00, 0x05, 11, 0x00, 0x00, 0x00, 0x00, 0x00])
    yield harness.host_expect_ack()

    # Send a few packets while we "process" the data as a slow host
    for i in range(2):
        yield harness.host_send_token_packet(PID.IN, 0, 0)
        yield harness.host_expect_nak()

    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("2. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), data, len(setup_data), len(setup_data) != 10))
    # Note: the `out` buffer hasn't been drained yet

    yield harness.set_response(epaddr_in, EndpointResponse.ACK)
    yield harness.host_send_token_packet(PID.IN, 0, 0)
    yield harness.host_expect_data_packet(PID.DATA1, [])
    yield harness.host_send_ack()

    for i in range(1532, 1541):
        yield harness.host_send_sof(i)


### STALL TEST
    harness.dut._log.info("sending a STALL test")
    # Send a SETUP packet without draining it on the device side
    yield harness.host_send_token_packet(PID.SETUP, 0, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])
    yield harness.host_expect_ack()

    # Send a few packets while we "process" the data as a slow host
    for i in range(2):
        yield harness.host_send_token_packet(PID.IN, 0, 0)
        yield harness.host_expect_nak()

    # Read the data, which should unblock the sending
    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("1. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), setup_data))
    yield harness.write(harness.csrs['usb_in_ctrl'], 0x40) # Set STALL

    # Perform the final "read"
    yield harness.host_send_token_packet(PID.IN, 0, 0)
    yield harness.host_expect_stall()
### RESUMING

    # Send a SETUP packet to the wrong endpoint
    harness.dut._log.info("sending a packet to the wrong endpoint that should be ignored")
    yield harness.host_send_token_packet(PID.SETUP, 11, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])
    # yield harness.host_expect_ack()

    yield harness.write(harness.csrs['usb_address'], 11)

### SETUP packet without draining
    harness.dut._log.info("sending a packet without draining SETUP")
    # Send a SETUP packet without draining it on the device side
    yield harness.host_send_token_packet(PID.SETUP, 11, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])
    yield harness.host_expect_ack()

    # Set it up so we ACK the final IN packet
    data = [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
            0x08, 0x09, 0x0A, 0x0B]
    for b in data:
        yield harness.write(harness.csrs['usb_in_data'], b)

    # Send a few packets while we "process" the data as a slow host
    for i in range(2):
        yield harness.host_send_token_packet(PID.IN, 11, 0)
        yield harness.host_expect_nak()

    # Read the data and queue the IN packet, which should unblock the sending
    harness.dut._log.info("draining SETUP which should unblock it")
    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("3. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), setup_data))
    yield harness.write(harness.csrs['usb_in_ctrl'], 0)

    # Perform the final send
    yield harness.host_send_token_packet(PID.IN, 11, 0)
    yield harness.host_expect_data_packet(PID.DATA1, data)
    yield harness.host_send_ack()



@cocotb.test()
def test_control_transfer_out_lazy(dut):
    """Test that we can transfer data out without immediately draining it"""
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

    ep3_out = EndpointType.epaddr(3, EndpointType.OUT)
    ep3_in = EndpointType.epaddr(3, EndpointType.IN)
    ep3_data = [9, 5, 3, 2]

    harness = UsbTest(dut)
    yield harness.reset()

    yield harness.connect()
    yield harness.write(harness.csrs['usb_address'], 0)
### SETUP packet

    # Set it up so that we can ack EP0 and EP3
    yield harness.write(harness.csrs['usb_out_ctrl'], 0x13) # Enable EP3

    harness.dut._log.info("sending initial SETUP packet")
    # Send a SETUP packet without draining it on the device side
    yield harness.host_send_token_packet(PID.SETUP, 0, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x80, 0x06, 0x00, 0x05, 0x00, 0x00, 0x0A, 0x00])
    yield harness.host_expect_ack()

    # Set it up so we ACK the final IN packet
    data = [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
            0x08, 0x09, 0x0A, 0x0B]
    for b in data:
        yield harness.write(harness.csrs['usb_in_data'], b)

    # Send a few packets while we "process" the data as a slow host
    harness.dut._log.info("\"processing\" data on a slow host (should send NAKs)")
    for i in range(2):
        yield harness.host_send_token_packet(PID.IN, 0, 0)
        yield harness.host_expect_nak()

    # Queue the response packet for transmission
    yield harness.write(harness.csrs['usb_in_ctrl'], 0)

    # Read the data, which drains it out of the SETUP buffer
    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("1. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), setup_data))

    # Perform the final "read"
    yield harness.host_recv(PID.DATA1, 0, 0, data)

    # Status stage
    yield harness.set_response(epaddr_out, EndpointResponse.ACK)
    yield harness.transaction_status_out(0, epaddr_out)


### OUT packet without draining
    harness.dut._log.info("sending a packet to EP3 without draining OUT")
    # Send a SETUP packet without draining it on the device side
    yield harness.host_send_token_packet(PID.OUT, 0, ep3_out)
    yield harness.host_send_data_packet(PID.DATA0, ep3_data)
    yield harness.host_expect_nak()

    harness.dut._log.info("draining OUT buffer")
    out_status = yield harness.read(harness.csrs['usb_out_status'])
    if (out_status & 0x20) == 0:
        raise TestFailure("out_status didn't have any pending event")
    if (out_status & 0x10) == 0:
        raise TestFailure("out_status didn't have any data")
    if (out_status & 0x0f) != 0:
        raise TestFailure("out_status was for ep {}, not ep 0".format(out_status & 0x0f))
    rx_data = yield harness.drain_out()
    harness.assertSequenceEqual([], rx_data, "wrong setup packet received")

    harness.dut._log.info("sending OUT to EP3 again")
    yield harness.host_send_token_packet(PID.OUT, 0, ep3_out)
    yield harness.host_send_data_packet(PID.DATA0, ep3_data)
    yield harness.host_expect_ack()
    out_status = yield harness.read(harness.csrs['usb_out_status'])
    if (out_status & 0x20) == 0:
        raise TestFailure("out_status didn't have any pending event")
    if (out_status & 0x10) == 0:
        raise TestFailure("out_status didn't have any data")
    if (out_status & 0x0f) != 3:
        raise TestFailure("out_status was for ep {}, not ep 3".format(out_status & 0x0f))
    rx_data = yield harness.drain_out()
    harness.assertSequenceEqual(ep3_data, rx_data, "wrong ep3 data packet received")


@cocotb.test()
def test_control_transfer_in_large(dut):
    """Test that we can transfer data in without immediately draining it"""
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

    harness = UsbTest(dut)
    yield harness.reset()

    yield harness.connect()
    yield harness.write(harness.csrs['usb_address'], 0)

    # Set address to 11
    yield harness.control_transfer_out(
        0,
        # Set address (to 11)
        [0x00, 0x05, 11, 0x00, 0x00, 0x00, 0x00, 0x00],
        # 18 byte descriptor, max packet size 8 bytes
        None,
    )
    yield harness.write(harness.csrs['usb_address'], 11)

    ### Send a packet that's longer than 64 bytes
    string_data = [
        0x4e, 0x3, 0x46, 0x0, 0x6f, 0x0, 0x6d, 0x0,
        0x75, 0x0, 0x20, 0x0, 0x44, 0x0, 0x46, 0x0,
        0x55, 0x0, 0x20, 0x0, 0x42, 0x0, 0x6f, 0x0,
        0x6f, 0x0, 0x74, 0x0, 0x6c, 0x0, 0x6f, 0x0,
        0x61, 0x0, 0x64, 0x0, 0x65, 0x0, 0x72, 0x0,
        0x20, 0x0, 0x76, 0x0, 0x31, 0x0, 0x2e, 0x0,
        0x38, 0x0, 0x2e, 0x0, 0x37, 0x0, 0x2d, 0x0,
        0x38, 0x0, 0x2d, 0x0, 0x67, 0x0, 0x31, 0x0,
        0x36, 0x0, 0x36, 0x0, 0x34, 0x0, 0x66, 0x0,
        0x33, 0x0, 0x35, 0x0, 0x0, 0x0
    ]

    # Send a SETUP packet without draining it on the device side
    yield harness.host_send_token_packet(PID.SETUP, 11, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x80, 0x06, 0x02, 0x03, 0x09, 0x04, 0xFF, 0x00])
    yield harness.host_expect_ack()
    yield harness.drain_setup()

    # Send a few packets while we "process" the data as a slow host
    for i in range(3):
        yield harness.host_send_token_packet(PID.IN, 11, 0)
        yield harness.host_expect_nak()

    datax = PID.DATA1
    sent_data = 0
    for i, chunk in enumerate(grouper_tofit(64, string_data)):
        sent_data = 1
        harness.dut._log.debug("Actual data we're expecting: {}".format(chunk))
        for b in chunk:
            yield harness.write(harness.csrs['usb_in_data'], b)
        yield harness.write(harness.csrs['usb_in_ctrl'], 0)
        recv = cocotb.fork(harness.host_recv(datax, 11, 0, chunk))
        yield recv.join()

        # Send a few packets while we "process" the data as a slow host
        for i in range(3):
            yield harness.host_send_token_packet(PID.IN, 11, 0)
            yield harness.host_expect_nak()

        if datax == PID.DATA0:
            datax = PID.DATA1
        else:
            datax = PID.DATA0
    if not sent_data:
        yield harness.write(harness.csrs['usb_in_ctrl'], 0)
        recv = cocotb.fork(harness.host_recv(datax, 11, 0, []))
        yield harness.send_data(datax, 0, string_data)
        yield recv.join()

    yield harness.set_response(epaddr_out, EndpointResponse.ACK)
    yield harness.host_send_token_packet(PID.OUT, 11, 0)
    yield harness.host_send_data_packet(PID.DATA0, [])
    yield harness.host_expect_ack()


@cocotb.test()
def test_sof_stuffing(dut):
    harness = UsbTest(dut)
    yield harness.reset()

    yield harness.connect()
    yield harness.host_send_sof(0x04ff)
    yield harness.host_send_sof(0x0512)
    yield harness.host_send_sof(0x06e1)
    yield harness.host_send_sof(0x0519)

@cocotb.test()
def test_sof_is_ignored(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    addr = 0
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
    yield harness.write(harness.csrs['usb_address'], addr)

    data = [0, 1, 8, 0, 4, 3, 0, 0]
    @cocotb.coroutine
    def send_setup_and_sof():
        # Send SOF packet
        yield harness.host_send_sof(2)

        # Setup stage
        # ------------------------------------------
        # Send SETUP packet
        yield harness.host_send_token_packet(PID.SETUP, addr, epaddr_out)

        # Send another SOF packet
        yield harness.host_send_sof(3)

        # Data stage
        # ------------------------------------------
        # Send DATA packet
        yield harness.host_send_data_packet(PID.DATA1, data)
        yield harness.host_expect_ack()

        # Send another SOF packet
        yield harness.host_send_sof(4)

    # Indicate that we're ready to receive data to EP0
    # harness.write(harness.csrs['usb_in_ctrl'], 0)

    xmit = cocotb.fork(send_setup_and_sof())
    yield harness.expect_setup(epaddr_out, data)
    yield xmit.join()

    # # Status stage
    # # ------------------------------------------
    yield harness.set_response(epaddr_out, EndpointResponse.ACK)
    yield harness.transaction_status_out(addr, epaddr_out)

@cocotb.test()
def test_control_setup_clears_stall(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    addr = 28
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
    yield harness.write(harness.csrs['usb_address'], addr)
    yield harness.host_send_sof(0)

    d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0, 0]
    setup_data = [0x80, 0x06, 0x00, 0x03, 0x00, 0x00, 0x00, 0x00]

    # Send the data -- just to ensure that things are working
    harness.dut._log.info("sending data to confirm things are working")
    yield harness.transaction_data_out(addr, epaddr_out, d)

    # Send it again to ensure we can re-queue things.
    harness.dut._log.info("sending data to confirm we can re-queue")
    yield harness.transaction_data_out(addr, epaddr_out, d)

    # STALL the endpoint now
    harness.dut._log.info("stalling EP0 IN")
    yield harness.write(harness.csrs['usb_in_ctrl'], 0x40)

    # Do another receive, which should fail
    harness.dut._log.info("next transaction should stall")
    yield harness.host_send_token_packet(PID.IN, addr, 0)
    yield harness.host_expect_stall()

    # Do a SETUP, which should pass
    harness.dut._log.info("doing a SETUP on EP0, which should clear the stall")
    yield harness.control_transfer_in(addr, setup_data)

    # Finally, do one last transfer, which should succeed now
    # that the endpoint is unstalled.
    harness.dut._log.info("doing an IN transfer to make sure it's cleared")
    yield harness.transaction_data_in(addr, epaddr_out, d, datax=PID.DATA1)

@cocotb.test()
def test_control_transfer_in_nak_data(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    addr = 22
    yield harness.write(harness.csrs['usb_address'], addr)
    # Get descriptor, Index 0, Type 03, LangId 0000, wLength 64
    setup_data = [0x80, 0x06, 0x00, 0x03, 0x00, 0x00, 0x40, 0x00]
    in_data = [0x04, 0x03, 0x09, 0x04]

    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
    # yield harness.clear_pending(epaddr_in)

    yield harness.write(harness.csrs['usb_address'], addr)

    # Setup stage
    # -----------
    yield harness.transaction_setup(addr, setup_data)

    # Data stage
    # -----------
    yield harness.set_response(epaddr_in, EndpointResponse.NAK)
    yield harness.host_send_token_packet(PID.IN, addr, epaddr_in)
    yield harness.host_expect_nak()

    yield harness.set_data(epaddr_in, in_data)
    yield harness.set_response(epaddr_in, EndpointResponse.ACK)
    yield harness.host_send_token_packet(PID.IN, addr, epaddr_in)
    yield harness.host_expect_data_packet(PID.DATA1, in_data)
    yield harness.host_send_ack()

# @cocotb.test()
# def test_control_transfer_in_nak_status(dut):
#     harness = UsbTest(dut)
#     yield harness.reset()
#     yield harness.connect()

#     addr = 20
#     setup_data = [0x00, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00]
#     out_data = [0x00, 0x01]

#     epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
#     epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
#     yield harness.clear_pending(epaddr_out)
#     yield harness.clear_pending(epaddr_in)

#     # Setup stage
#     # -----------
#     yield harness.transaction_setup(addr, setup_data)

#     # Data stage
#     # ----------
#     yield harness.set_response(epaddr_out, EndpointResponse.ACK)
#     yield harness.transaction_data_out(addr, epaddr_out, out_data)

#     # Status stage
#     # ----------
#     yield harness.set_response(epaddr_in, EndpointResponse.NAK)

#     yield harness.host_send_token_packet(PID.IN, addr, epaddr_in)
#     yield harness.host_expect_nak()

#     yield harness.host_send_token_packet(PID.IN, addr, epaddr_in)
#     yield harness.host_expect_nak()

#     yield harness.set_response(epaddr_in, EndpointResponse.ACK)
#     yield harness.host_send_token_packet(PID.IN, addr, epaddr_in)
#     yield harness.host_expect_data_packet(PID.DATA1, [])
#     yield harness.host_send_ack()
#     yield harness.clear_pending(epaddr_in)


@cocotb.test()
def test_control_transfer_in(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    yield harness.clear_pending(EndpointType.epaddr(0, EndpointType.OUT))
    yield harness.clear_pending(EndpointType.epaddr(0, EndpointType.IN))
    yield harness.write(harness.csrs['usb_address'], 20)
    yield harness.host_send_sof(0)

    yield harness.control_transfer_in(
        20,
        # Get descriptor, Index 0, Type 03, LangId 0000, wLength 10?
        [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00],
        # 12 byte descriptor, max packet size 8 bytes
        [0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
            0x08, 0x09, 0x0A, 0x0B],
    )

@cocotb.test()
def test_control_transfer_in_out(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    yield harness.clear_pending(EndpointType.epaddr(0, EndpointType.OUT))
    yield harness.clear_pending(EndpointType.epaddr(0, EndpointType.IN))
    yield harness.write(harness.csrs['usb_address'], 0)

    yield harness.control_transfer_in(
        0,
        # Get device descriptor
        [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 00],
        # 18 byte descriptor, max packet size 8 bytes
        [0x12, 0x01, 0x10, 0x02, 0x02, 0x00, 0x00, 0x40,
            0x09, 0x12, 0xB1, 0x70, 0x01, 0x01, 0x01, 0x02,
            00, 0x01],
    )

    yield harness.control_transfer_out(
        0,
        # Set address (to 11)
        [0x00, 0x05, 0x0B, 0x00, 0x00, 0x00, 0x00, 0x00],
        # 18 byte descriptor, max packet size 8 bytes
        None,
    )


@cocotb.test()
def test_control_transfer_in_out_in(dut):
    """This transaction is pretty much the first thing any OS will do"""
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    yield harness.clear_pending(EndpointType.epaddr(0, EndpointType.OUT))
    yield harness.clear_pending(EndpointType.epaddr(0, EndpointType.IN))
    yield harness.write(harness.csrs['usb_address'], 0)

    yield harness.control_transfer_in(
        0,
        # Get device descriptor
        [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 00],
        # 18 byte descriptor, max packet size 8 bytes
        [0x12, 0x01, 0x10, 0x02, 0x02, 0x00, 0x00, 0x40,
         0x09, 0x12, 0xB1, 0x70, 0x01, 0x01, 0x01, 0x02,
         00, 0x01],
    )

    yield harness.control_transfer_out(
        0,
        # Set address (to 11)
        [0x00, 0x05, 11, 0x00, 0x00, 0x00, 0x00, 0x00],
        # 18 byte descriptor, max packet size 8 bytes
        None,
    )

    yield harness.write(harness.csrs['usb_address'], 11)

    yield harness.control_transfer_in(
        11,
        # Get device descriptor
        [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 00],
        # 18 byte descriptor, max packet size 8 bytes
        [0x12, 0x01, 0x10, 0x02, 0x02, 0x00, 0x00, 0x40,
         0x09, 0x12, 0xB1, 0x70, 0x01, 0x01, 0x01, 0x02,
         00, 0x01],
    )

@cocotb.test()
def test_control_transfer_out_in(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    yield harness.clear_pending(EndpointType.epaddr(0, EndpointType.OUT))
    yield harness.clear_pending(EndpointType.epaddr(0, EndpointType.IN))
    yield harness.write(harness.csrs['usb_address'], 0)

    yield harness.control_transfer_out(
        0,
        # Set address (to 20)
        [0x00, 0x05, 20, 0x00, 0x00, 0x00, 0x00, 0x00],
        # 18 byte descriptor, max packet size 8 bytes
        None,
    )

    yield harness.write(harness.csrs['usb_address'], 20)

    yield harness.control_transfer_in(
        20,
        # Get device descriptor
        [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 00],
        # 18 byte descriptor, max packet size 8 bytes
        [0x12, 0x01, 0x10, 0x02, 0x02, 0x00, 0x00, 0x40,
            0x09, 0x12, 0xB1, 0x70, 0x01, 0x01, 0x01, 0x02,
            00, 0x01],
    )

@cocotb.test()
def test_control_transfer_out_nak_data(dut):
    """Send more than one packet of OUT data, and ensure the second packet is NAKed"""

    """Test that we can transfer data in without immediately draining it"""
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

    harness = UsbTest(dut)
    yield harness.reset()

    yield harness.connect()
    yield harness.write(harness.csrs['usb_address'], 0)

    # Enable OUT endpoint
    yield harness.write(harness.csrs['usb_out_ctrl'], 0x10)

### SET ADDRESS
    harness.dut._log.info("setting address")
    # Set the address.  Again, don't drain the device side yet.
    yield harness.host_send_token_packet(PID.SETUP, 0, epaddr_out)
    yield harness.host_send_data_packet(PID.DATA0, [0x00, 0x05, 11, 0x00, 0x00, 0x00, 0x00, 0x00])
    yield harness.host_expect_ack()

    # Send a few packets while we "process" the data as a slow host
    for i in range(2):
        yield harness.host_send_token_packet(PID.IN, 0, 0)
        yield harness.host_expect_nak()

    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("2. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), data, len(setup_data), len(setup_data) != 10))
    # Note: the `out` buffer hasn't been drained yet
    yield harness.write(harness.csrs['usb_in_ctrl'], 0) # Respond ACK to this packet
    yield harness.host_send_token_packet(PID.IN, 0, 0)
    yield harness.host_expect_data_packet(PID.DATA1, [])
    yield harness.host_send_ack()
    yield harness.write(harness.csrs['usb_address'], 11)

### GET STATUS
    harness.dut._log.info("getting status")
    yield harness.write(harness.csrs['usb_in_ev_pending'], 0xff)
    harness.dut._log.info("sending DFU GET_STATUS command")
    yield harness.control_transfer_in(11,
        [0xA1, 0x03, 0x00, 0x00, 0x00, 0x00, 0x06, 0x00],
        [0x00, 0x05, 0x00, 0x00, 0x02, 0x00])
   

### STALL TEST
    harness.dut._log.info("testing stall")
    out_data = []
    for i in range(64):
        out_data.append(i)

    # Send a SETUP packet without draining it on the device side
    harness.dut._log.info("sending SETUP initiating large transfer")
    yield harness.host_send_token_packet(PID.SETUP, 11, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x21, 0x06, 0x00, 0x06, 0x00, 0x00, 0x00, 0x03])
    yield harness.host_expect_ack()

    # Send a packet while we "process" the data as a slow host
    yield harness.host_send_token_packet(PID.OUT, 11, 0)
    yield harness.host_send_data_packet(PID.DATA1, out_data)
    yield harness.host_expect_nak()

    # Read the data, which should unblock the sending
    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("1. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), setup_data))
    yield harness.write(harness.csrs['usb_out_ctrl'], 0x10) # Enable response on OUT EP

    # Perform the final "write"
    yield harness.host_send_token_packet(PID.OUT, 11, 0)
    yield harness.host_send_data_packet(PID.DATA1, out_data)
    yield harness.host_expect_ack()

    out_compare_data = yield harness.drain_out()
    harness.assertSequenceEqual(out_data, out_compare_data, "first packet not equal")

    # Send second packet of OUT data
    out_data = []
    for i in range(64):
        out_data.append(0x20 + i)
    harness.dut._log.info("sending second transaction of large transfer (should succeed)")
    yield harness.host_send_token_packet(PID.OUT, 11, epaddr_out)
    yield harness.host_send_data_packet(PID.DATA1, out_data)
    yield harness.host_expect_ack()

    # Send third packet of OUT data
    for i in range(2):
        harness.dut._log.info("sending third transaction of large transfer (should NAK)")
        yield harness.host_send_token_packet(PID.OUT, 11, epaddr_out)
        yield harness.host_send_data_packet(PID.DATA0, out_data)
        yield harness.host_expect_nak()

    # Drain the OUT buffer and try again
    harness.dut._log.info("sending third transaction of large transfer (should ACK)")
    out_compare_data = yield harness.drain_out()
    harness.assertSequenceEqual(out_data, out_compare_data, "second packet not equal")
    out_data = []
    for i in range(64):
        out_data.append(0x40 + i)
    yield harness.host_send_token_packet(PID.OUT, 11, epaddr_out)
    yield harness.host_send_data_packet(PID.DATA0, out_data)
    yield harness.host_expect_ack()
### CONFIRM SEQUENCE
    yield harness.set_response(epaddr_in, EndpointResponse.ACK)
    yield harness.host_send_token_packet(PID.IN, 11, 0)
    yield harness.host_expect_data_packet(PID.DATA1, [])
    yield harness.host_send_ack()
    out_compare_data = yield harness.drain_out()
    harness.assertSequenceEqual(out_data, out_compare_data, "third packet not equal")

@cocotb.test()
def test_in_transfer(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    addr = 0
    epaddr = EndpointType.epaddr(1, EndpointType.IN)
    yield harness.write(harness.csrs['usb_address'], addr)

    d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8]

    yield harness.clear_pending(epaddr)
    yield harness.set_response(epaddr, EndpointResponse.NAK)

    yield harness.set_data(epaddr, d[:4])
    yield harness.set_response(epaddr, EndpointResponse.ACK)
    yield harness.host_send_token_packet(PID.IN, addr, epaddr)
    yield harness.host_expect_data_packet(PID.DATA1, d[:4])
    yield harness.host_send_ack()

    pending = yield harness.pending(epaddr)
    if pending:
        raise TestFailure("data was still pending")
    yield harness.clear_pending(epaddr)
    yield harness.set_data(epaddr, d[4:])
    yield harness.set_response(epaddr, EndpointResponse.ACK)

    yield harness.host_send_token_packet(PID.IN, addr, epaddr)
    yield harness.host_expect_data_packet(PID.DATA1, d[4:])
    yield harness.host_send_ack()

@cocotb.test()
def test_out_transfer(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()
    ep0 = EndpointType.epaddr(0, EndpointType.OUT)
    ep1 = EndpointType.epaddr(1, EndpointType.OUT)
    ep2 = EndpointType.epaddr(2, EndpointType.OUT)

    harness.dut._log.info("setting address")
    addr = 27
    yield harness.host_send_token_packet(PID.SETUP, 0, ep0)
    yield harness.host_send_data_packet(PID.DATA0, [0x00, 0x05, addr, 0x00, 0x00, 0x00, 0x00, 0x00])
    yield harness.host_expect_ack()
    yield harness.write(harness.csrs['usb_address'], addr)

    d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0x7, 0x8]

    yield harness.clear_pending(ep1)
    yield harness.clear_pending(ep2)
    yield harness.set_response(ep1, EndpointResponse.NAK)
    yield harness.set_response(ep2, EndpointResponse.NAK)

    harness.dut._log.info("sending data packet to EP1")
    yield harness.set_response(ep1, EndpointResponse.ACK)
    yield harness.host_send_token_packet(PID.OUT, addr, ep1)
    yield harness.host_send_data_packet(PID.DATA0, d[:4])
    yield harness.host_expect_ack()

    harness.dut._log.info("verifying packet on EP1")
    pending = yield harness.pending(ep1)
    yield RisingEdge(harness.dut.clk12)
    if not pending:
        raise TestFailure("data was not received")

    harness.dut._log.info("sending data to EP2 without clearing EP1")
    yield harness.host_send_token_packet(PID.OUT, addr, ep2)
    yield harness.host_send_data_packet(PID.DATA0, d[4:])
    yield harness.host_expect_nak()

    harness.dut._log.info("sending to EP2 data after clearing EP1 without priming EP2")
    yield harness.clear_pending(ep1)
    yield harness.host_send_token_packet(PID.OUT, addr, ep2)
    yield harness.host_send_data_packet(PID.DATA0, d[4:])
    yield harness.host_expect_nak()

    harness.dut._log.info("sending data packet to EP2")
    yield harness.set_response(ep2, EndpointResponse.ACK)

    yield harness.host_send_token_packet(PID.OUT, addr, ep2)
    yield harness.host_send_data_packet(PID.DATA0, d[4:])
    yield harness.host_expect_ack()

@cocotb.test()
def test_in_transfer_stuff_last(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    addr = 0
    epaddr = EndpointType.epaddr(1, EndpointType.IN)
    yield harness.write(harness.csrs['usb_address'], addr)

    d = [0x37, 0x75, 0x00, 0xe0]

    yield harness.clear_pending(epaddr)
    yield harness.set_response(epaddr, EndpointResponse.NAK)

    yield harness.set_data(epaddr, d)
    yield harness.set_response(epaddr, EndpointResponse.ACK)
    yield harness.host_send_token_packet(PID.IN, addr, epaddr)
    yield harness.host_expect_data_packet(PID.DATA1, d)

@cocotb.test()
def test_debug_in(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    addr = 0
    yield harness.write(harness.csrs['usb_address'], addr)
    # The "scratch" register defaults to 0x12345678 at boot.
    reg_addr = harness.csrs['ctrl_scratch']
    setup_data = [0xc3, 0x00,
                    (reg_addr >> 0) & 0xff,
                    (reg_addr >> 8) & 0xff,
                    (reg_addr >> 16) & 0xff,
                    (reg_addr >> 24) & 0xff, 0x04, 0x00]
    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)

    yield harness.transaction_data_in(addr, epaddr_in, [0x2, 0x4, 0x6, 0x8, 0xa], chunk_size=64)

    yield harness.clear_pending(epaddr_out)
    yield harness.clear_pending(epaddr_in)

    # Setup stage
    yield harness.host_send_token_packet(PID.SETUP, addr, epaddr_out)
    yield harness.host_send_data_packet(PID.DATA0, setup_data)
    yield harness.host_expect_ack()

    # Data stage
    yield harness.host_send_token_packet(PID.IN, addr, epaddr_in)
    yield harness.host_expect_data_packet(PID.DATA1, [0x12, 0, 0, 0])
    yield harness.host_send_ack()

    # Status stage
    yield harness.host_send_token_packet(PID.OUT, addr, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA1, [])
    yield harness.host_expect_ack()

# @cocotb.test()
# def test_debug_in_missing_ack(dut):
#     harness = UsbTest(dut)
#     yield harness.reset()
#     yield harness.connect()

#     addr = 28
#     reg_addr = harness.csrs['ctrl_scratch']
#     setup_data = [0xc3, 0x00,
#                     (reg_addr >> 0) & 0xff,
#                     (reg_addr >> 8) & 0xff,
#                     (reg_addr >> 16) & 0xff,
#                     (reg_addr >> 24) & 0xff, 0x04, 0x00]
#     epaddr_in = EndpointType.epaddr(0, EndpointType.IN)
#     epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)

#     # Setup stage
#     yield harness.host_send_token_packet(PID.SETUP, addr, epaddr_out)
#     yield harness.host_send_data_packet(PID.DATA0, setup_data)
#     yield harness.host_expect_ack()

#     # Data stage (missing ACK)
#     yield harness.host_send_token_packet(PID.IN, addr, epaddr_in)
#     yield harness.host_expect_data_packet(PID.DATA1, [0x12, 0, 0, 0])

#     # Data stage
#     yield harness.host_send_token_packet(PID.IN, addr, epaddr_in)
#     yield harness.host_expect_data_packet(PID.DATA1, [0x12, 0, 0, 0])
#     yield harness.host_send_ack()

#     # Status stage
#     yield harness.host_send_token_packet(PID.OUT, addr, epaddr_out)
#     yield harness.host_send_data_packet(PID.DATA1, [])
#     yield harness.host_expect_ack()

@cocotb.test()
def test_debug_out(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    addr = 0
    yield harness.write(harness.csrs['usb_address'], addr)
    reg_addr = harness.csrs['ctrl_scratch']
    setup_data = [0x43, 0x00,
                    (reg_addr >> 0) & 0xff,
                    (reg_addr >> 8) & 0xff,
                    (reg_addr >> 16) & 0xff,
                    (reg_addr >> 24) & 0xff, 0x04, 0x00]
    ep0in_addr = EndpointType.epaddr(0, EndpointType.IN)
    ep1in_addr = EndpointType.epaddr(1, EndpointType.IN)
    ep0out_addr = EndpointType.epaddr(0, EndpointType.OUT)

    # Force Wishbone to acknowledge the packet
    yield harness.clear_pending(ep0out_addr)
    yield harness.clear_pending(ep0in_addr)
    yield harness.clear_pending(ep1in_addr)

    # Setup stage
    yield harness.host_send_token_packet(PID.SETUP, addr, ep0out_addr)
    yield harness.host_send_data_packet(PID.DATA0, setup_data)
    yield harness.host_expect_ack()

    # Data stage
    yield harness.host_send_token_packet(PID.OUT, addr, ep0out_addr)
    yield harness.host_send_data_packet(PID.DATA1, [0x42, 0, 0, 0])
    yield harness.host_expect_ack()

    # Status stage (wrong endopint)
    yield harness.host_send_token_packet(PID.IN, addr, ep1in_addr)
    yield harness.host_expect_nak()

    # Status stage
    yield harness.host_send_token_packet(PID.IN, addr, ep0in_addr)
    yield harness.host_expect_data_packet(PID.DATA1, [])
    yield harness.host_send_ack()

    new_value = yield harness.read(reg_addr)
    if new_value != 0x42:
        raise TestFailure("memory at 0x{:08x} should be 0x{:08x}, but memory value was 0x{:08x}".format(reg_Addr, 0x42, new_value))

@cocotb.test()
def test_stall_in(dut):
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    yield harness.control_transfer_in(
        0,
        # Get device descriptor
        [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 00],
        # 18 byte descriptor, max packet size 8 bytes
        [0x12, 0x01, 0x10, 0x02, 0x02, 0x00, 0x00, 0x40,
            0x09, 0x12, 0xB1, 0x70, 0x01, 0x01, 0x01, 0x02,
            00, 0x01],
    )

### STALL TRANSACTION
    # Send a SETUP packet without draining it on the device side
    yield harness.host_send_token_packet(PID.SETUP, 0, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])
    yield harness.host_expect_ack()

    # Send a few packets while we "process" the data as a slow host
    for i in range(10):
        yield harness.host_send_token_packet(PID.IN, 0, 0)
        yield harness.host_expect_nak()

    # Read the data, which should unblock the sending
    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("1. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), setup_data))
    yield harness.write(harness.csrs['usb_in_ctrl'], 0x40) # Set STALL

    # Perform the final "read"
    yield harness.host_send_token_packet(PID.IN, 0, 0)
    yield harness.host_expect_stall()

### STALL TRANSACTION
    harness.dut._log.info("stall transaction")
    # Send a SETUP packet without draining it on the device side
    yield harness.host_send_token_packet(PID.SETUP, 0, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00])
    yield harness.host_expect_ack()

    # Send a few packets while we "process" the data as a slow host
    for i in range(2):
        yield harness.host_send_token_packet(PID.IN, 0, 0)
        yield harness.host_expect_nak()

    # Read the data, which should unblock the sending
    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("1. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), setup_data))
    yield harness.write(harness.csrs['usb_in_ctrl'], 0x40) # Set STALL

    # Perform the final "read"
    yield harness.host_send_token_packet(PID.IN, 0, 0)
    yield harness.host_expect_stall()

### NORMAL TRANSACTION
    harness.dut._log.info("normal transaction")
    # Finally, ensure the host returns after the stall.
    yield harness.control_transfer_in(
        0,
        # Get device descriptor
        [0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 00],
        # 18 byte descriptor, max packet size 8 bytes
        [0x12, 0x01, 0x10, 0x02, 0x02, 0x00, 0x00, 0x40,
            0x09, 0x12, 0xB1, 0x70, 0x01, 0x01, 0x01, 0x02,
            00, 0x01],
    )

@cocotb.test()
def test_stall_out(dut):
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

    d = [0x12, 0x01, 0x10, 0x02, 0x02, 0x00, 0x00, 0x40,
            0x09, 0x12]

    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    yield harness.control_transfer_out(
        0,
        # Get device descriptor
        [0x00, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 00],
        # 18 byte descriptor, max packet size 8 bytes
        [0x12, 0x01, 0x10, 0x02, 0x02, 0x00, 0x00, 0x40,
            0x09, 0x12, 0xB1, 0x70, 0x01, 0x01, 0x01, 0x02,
            00, 0x01],
    )

### STALL TRANSACTION
    # Send a SETUP packet without draining it on the device side
    yield harness.host_send_token_packet(PID.SETUP, 0, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x00, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])
    yield harness.host_expect_ack()

    # Send a few packets while we "process" the data as a slow host
    for i in range(3):
        yield harness.host_send_token_packet(PID.OUT, 0, 0)
        yield harness.host_send_data_packet(PID.DATA1, d)
        yield harness.host_expect_nak()

    # Read the data, which should unblock the sending
    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("1. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), setup_data))
    yield harness.write(harness.csrs['usb_out_ctrl'], 0x40) # Set STALL

    # Perform the final "read"
    yield harness.host_send_token_packet(PID.OUT, 0, 0)
    yield harness.host_send_data_packet(PID.DATA1, d)
    yield harness.host_expect_stall()

### STALL TRANSACTION
    harness.dut._log.info("stall transaction")
    # Send a SETUP packet without draining it on the device side
    yield harness.host_send_token_packet(PID.SETUP, 0, epaddr_in)
    yield harness.host_send_data_packet(PID.DATA0, [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0x00])
    yield harness.host_expect_ack()

    # Send a few packets while we "process" the data as a slow host
    for i in range(2):
        yield harness.host_send_token_packet(PID.OUT, 0, 0)
        yield harness.host_send_data_packet(PID.DATA1, d)
        yield harness.host_expect_nak()

    # Read the data, which should unblock the sending
    setup_data = yield harness.drain_setup()
    if len(setup_data) != 10:
        raise TestFailure("1. expected setup data to be 10 bytes, but was {} bytes: {}".format(len(setup_data), setup_data))
    yield harness.write(harness.csrs['usb_out_ctrl'], 0x40) # Set STALL

    # Perform the final "read"
    yield harness.host_send_token_packet(PID.OUT, 0, 0)
    yield harness.host_send_data_packet(PID.DATA1, d)
    yield harness.host_expect_stall()

### NORMAL TRANSACTION
    harness.dut._log.info("normal transaction")
    # Finally, ensure the host returns after the stall.
    yield harness.control_transfer_out(
        0,
        # Get device descriptor
        [0x00, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 00],
        # 18 byte descriptor, max packet size 8 bytes
        [0x12, 0x01, 0x10, 0x02, 0x02, 0x00, 0x00, 0x40,
            0x09, 0x12, 0xB1, 0x70, 0x01, 0x01, 0x01, 0x02,
            00, 0x01],
    )

@cocotb.test()
def test_reset(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    yield harness.write(harness.csrs['usb_address'], 23)
    val = yield harness.read(harness.csrs['usb_address'])
    if val != 23:
        raise TestFailure("usb address should have been 23, but was {}".format(val))

    # SE0 condition
    harness.dut.usb_d_p = 0
    harness.dut.usb_d_n = 0
    for i in range(0, 64):
        yield RisingEdge(harness.dut.clk12)

    harness.dut.usb_d_p = 1
    harness.dut.usb_d_n = 0
    for i in range(0, 64):
        yield RisingEdge(harness.dut.clk12)

    val = yield harness.read(harness.csrs['usb_address'])
    if val != 0:
        raise TestFailure("after reset, usb address should have been 0, but was {}".format(val))

@cocotb.test()
def out_nak_different_ep(dut):
    """Send more than one packet of OUT data, and ensure the second packet is NAKed"""

    """Test that we can transfer data in without immediately draining it"""
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

    epaddr_d_out = EndpointType.epaddr(3, EndpointType.OUT)
    epaddr__in = EndpointType.epaddr(3, EndpointType.IN)

    harness = UsbTest(dut)
    yield harness.reset()

    yield harness.connect()
    yield harness.write(harness.csrs['usb_address'], 0)

### Enable OUT endpoint
    harness.dut._log.info("enabling OUT endpoint")
    yield harness.write(harness.csrs['usb_out_ctrl'], 0x10)

### SEND FIRST PACKET
    harness.dut._log.info("sending first packet")
    # Set the address.  Again, don't drain the device side yet.
    yield harness.host_send_token_packet(PID.OUT, 0, epaddr_d_out)
    yield harness.set_response(epaddr_d_out, EndpointResponse.ACK)
    yield harness.host_send_data_packet(PID.DATA0, [0x00, 0x05, 11, 0x00, 0x00, 0x00, 0x00, 0x00])
    yield harness.host_expect_ack()

### VERIFY DEVICE IS BUSY
    harness.dut._log.info("verifying device is busy")
    # Send a few packets while we "process" the data as a slow host
    yield harness.host_send_token_packet(PID.OUT, 0, epaddr_d_out)
    yield harness.host_send_data_packet(PID.DATA1, [0x00, 0x05, 11, 0x00, 0x00, 0x00, 0x00, 0x00])
    yield harness.host_expect_nak()

### VERIFY DEVICE SEES CORRECT EP NUMBER
    harness.dut._log.info("verifying device sees correct ep number")
    incoming_ep = yield harness.read(harness.csrs['usb_out_status'])
    if (incoming_ep & 0xf) != 3:
        raise TestFailure("incorrect first-stage incoming EP.  Expected 3, got: {} (status: {:02x})".format(incoming_ep & 0xf, incoming_ep))

### SEND PACKET TO A DIFFERENT EP WITHOUT DRAINING
    harness.dut._log.info("sending packet to different ep without draining")
    # Send a few packets while we "process" the data as a slow host
    yield harness.host_send_token_packet(PID.OUT, 0, epaddr_out)
    yield harness.host_send_data_packet(PID.DATA1, [0x02, 0x02, 14, 0x10, 0x00, 0x00, 0x00, 0x00])
    yield harness.host_expect_nak()

### VERIFY DEVICE STILL SEES CORRECT ADDRESS
    harness.dut._log.info("verifying device still sees correct address")
    incoming_ep = yield harness.read(harness.csrs['usb_out_status'])
    if (incoming_ep & 0xf) != 3:
        raise TestFailure("incorrect first-stage incoming EP.  Expected 3, got: {} (status: {:02x})".format(incoming_ep & 0xf, incoming_ep))
