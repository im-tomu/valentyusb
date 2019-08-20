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
        yield self.write(self.csrs['usb_enable_out0'], 0xff)
        yield self.write(self.csrs['usb_enable_out1'], 0xff)
        yield self.write(self.csrs['usb_enable_in0'], 0xff)
        yield self.write(self.csrs['usb_enable_in1'], 0xff)

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
    def clear_pending(self, _ep):
        yield Timer(0)

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
        for i in range(0, 1024):
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
            val = yield self.read(self.csrs['usb_epin_status'])
        else:
            val = yield self.read(self.csrs['usb_epout_status'])
        raise ReturnValue(val & 1)

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
            raise TestFailure("data was short (got {}, expected {})".format(expected_data, actual_data))
        actual_data, actual_crc16 = actual_data[:-2], actual_data[-2:]

        self.print_ep(epaddr, "Got: %r (expected: %r)", actual_data, expected_data)
        self.assertSequenceEqual(expected_data, actual_data, "SETUP packet not received")
        self.assertSequenceEqual(crc16(expected_data), actual_crc16, "CRC16 not valid")

    @cocotb.coroutine
    def expect_data(self, epaddr, expected_data, expected):
        actual_data = []
        # wait for data to appear
        for i in range(128):
            self.dut._log.debug("Prime loop {}".format(i))
            status = yield self.read(self.csrs['usb_epout_status'])
            have = status & 1
            if have:
                break
            yield RisingEdge(self.dut.clk12)

        for i in range(256):
            self.dut._log.debug("Read loop {}".format(i))
            status = yield self.read(self.csrs['usb_epout_status'])
            have = status & 1
            if not have:
                break
            v = yield self.read(self.csrs['usb_epout_data'])
            yield self.write(self.csrs['usb_epout_ctrl'], 3)
            actual_data.append(v)
            yield RisingEdge(self.dut.clk12)

        if expected == PID.ACK:
            if len(actual_data) < 2:
                raise TestFailure("data {} was short".format(actual_data))
            actual_data, actual_crc16 = actual_data[:-2], actual_data[-2:]

            self.print_ep(epaddr, "Got: %r (expected: %r)", actual_data, expected_data)
            self.assertSequenceEqual(expected_data, actual_data, "DATA packet not correctly received")
            self.assertSequenceEqual(crc16(expected_data), actual_crc16, "CRC16 not valid")

    @cocotb.coroutine
    def set_response(self, ep, response):
        if EndpointType.epdir(ep) == EndpointType.IN and response == EndpointResponse.ACK:
            yield self.write(self.csrs['usb_epin_epno'], EndpointType.epnum(ep))

    @cocotb.coroutine
    def send_data(self, token, ep, data):
        for b in data:
            yield self.write(self.csrs['usb_epin_data'], b)
        yield self.write(self.csrs['usb_epin_epno'], ep)

    @cocotb.coroutine
    def transaction_setup(self, addr, data, epnum=0):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

        xmit = cocotb.fork(self.host_setup(addr, epnum, data))
        yield self.expect_setup(epaddr_out, data)
        yield xmit.join()

    @cocotb.coroutine
    def transaction_data_out(self, addr, ep, data, chunk_size=64, expected=PID.ACK):
        epnum = EndpointType.epnum(ep)
        datax = PID.DATA1

        # # Set it up so we ACK the final IN packet
        # yield self.write(self.csrs['usb_epin_epno'], 0)
        for _i, chunk in enumerate(grouper_tofit(chunk_size, data)):
            self.dut._log.warning("Sening {} bytes to host".format(len(chunk)))
            # Enable receiving data
            yield self.write(self.csrs['usb_epout_ctrl'], (1 << 1))
            xmit = cocotb.fork(self.host_send(datax, addr, epnum, chunk, expected))
            yield self.expect_data(epnum, list(chunk), expected)
            yield xmit.join()

            if datax == PID.DATA0:
                datax = PID.DATA1
            else:
                datax = PID.DATA0

    @cocotb.coroutine
    def transaction_data_in(self, addr, ep, data, chunk_size=64):
        epnum = EndpointType.epnum(ep)
        datax = PID.DATA1
        sent_data = 0
        for i, chunk in enumerate(grouper_tofit(chunk_size, data)):
            sent_data = 1
            self.dut._log.debug("Actual data we're expecting: {}".format(chunk))
            for b in chunk:
                yield self.write(self.csrs['usb_epin_data'], b)
            yield self.write(self.csrs['usb_epin_epno'], epnum)
            recv = cocotb.fork(self.host_recv(datax, addr, epnum, chunk))
            yield recv.join()

            if datax == PID.DATA0:
                datax = PID.DATA1
            else:
                datax = PID.DATA0
        if not sent_data:
            yield self.write(self.csrs['usb_epin_epno'], epnum)
            recv = cocotb.fork(self.host_recv(datax, addr, epnum, []))
            yield self.send_data(datax, epnum, data)
            yield recv.join()

    @cocotb.coroutine
    def set_data(self, ep, data):
        _epnum = EndpointType.epnum(ep)
        for b in data:
            yield self.write(self.csrs['usb_epin_data'], b)

    @cocotb.coroutine
    def transaction_status_in(self, addr, ep):
        epnum = EndpointType.epnum(ep)
        assert EndpointType.epdir(ep) == EndpointType.IN
        xmit = cocotb.fork(self.host_recv(PID.DATA1, addr, epnum, []))
        yield xmit.join()

    @cocotb.coroutine
    def transaction_status_out(self, addr, ep):
        epnum = EndpointType.epnum(ep)
        assert EndpointType.epdir(ep) == EndpointType.OUT
        xmit = cocotb.fork(self.host_send(PID.DATA1, addr, epnum, []))
        yield xmit.join()

    @cocotb.coroutine
    def control_transfer_out(self, addr, setup_data, descriptor_data=None):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

        if (setup_data[0] & 0x80) == 0x80:
            raise Exception("setup_data indicated an IN transfer, but you requested an OUT transfer")

        # Setup stage
        self.dut._log.info("setup stage")
        yield self.transaction_setup(addr, setup_data)

        # Data stage
        if (setup_data[7] != 0 or setup_data[6] != 0) and descriptor_data is None:
            raise Exception("setup_data indicates data, but no descriptor data was specified")
        if (setup_data[7] == 0 and setup_data[6] == 0) and descriptor_data is not None:
            raise Exception("setup_data indicates no data, but descriptor data was specified")
        if descriptor_data is not None:
            self.dut._log.info("data stage")
            yield self.transaction_data_out(addr, epaddr_out, descriptor_data)

        # Status stage
        self.dut._log.info("status stage")
        # yield self.set_response(epaddr_in, EndpointResponse.ACK)
        yield self.transaction_status_in(addr, epaddr_in)

    @cocotb.coroutine
    def control_transfer_in(self, addr, setup_data, descriptor_data=None):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

        if (setup_data[0] & 0x80) == 0x00:
            raise Exception("setup_data indicated an OUT transfer, but you requested an IN transfer")

        # Setup stage
        self.dut._log.info("setup stage")
        yield self.transaction_setup(addr, setup_data)

        # Data stage
        # Data stage
        if (setup_data[7] != 0 or setup_data[6] != 0) and descriptor_data is None:
            raise Exception("setup_data indicates data, but no descriptor data was specified")
        if (setup_data[7] == 0 and setup_data[6] == 0) and descriptor_data is not None:
            raise Exception("setup_data indicates no data, but descriptor data was specified")
        if descriptor_data is not None:
            self.dut._log.info("data stage")
            yield self.transaction_data_in(addr, epaddr_in, descriptor_data)

        # Status stage
        self.dut._log.info("status stage")
        # yield self.set_response(epaddr_in, EndpointResponse.ACK)
        yield self.transaction_status_out(addr, epaddr_out)

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
    #   012345   0123
    # 0b011100 0b1000
    yield harness.write(harness.csrs['usb_address'], 28)
    yield harness.transaction_setup(28, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x00, 0x00])
    yield harness.transaction_data_in(28, 0, [])

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

    addr = 0x20
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
        yield harness.host_send_token_packet(PID.SETUP, addr, EndpointType.epnum(epaddr_out))

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
    # harness.write(harness.csrs['usb_epin_epno'], 0)

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

    d = [0x1, 0x2, 0x3, 0x4, 0x5, 0x6, 0, 0]

    # Send the data -- just to ensure that things are working
    yield harness.transaction_data_out(addr, epaddr_out, d)

    # Send it again to ensure we can re-queue things.
    yield harness.transaction_data_out(addr, epaddr_out, d)

    # STALL the endpoint now
    yield harness.write(harness.csrs['usb_enable_out0'], 0)
    yield harness.write(harness.csrs['usb_enable_out1'], 0)
    yield harness.write(harness.csrs['usb_enable_in0'], 0)
    yield harness.write(harness.csrs['usb_enable_in1'], 0)

    # Do another receive, which should fail
    yield harness.transaction_data_out(addr, epaddr_out, d, expected=PID.STALL)

    # Do a SETUP, which should pass
    yield harness.write(harness.csrs['usb_enable_out0'], 1)
    yield harness.control_transfer_out(addr, d)

    # Finally, do one last transfer, which should succeed now
    # that the endpoint is unstalled.
    yield harness.transaction_data_out(addr, epaddr_out, d)

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

    yield harness.control_transfer_out(
        20,
        # Set address (to 11)
        [0x00, 0x05, 0x0B, 0x00, 0x00, 0x00, 0x00, 0x00],
        # 18 byte descriptor, max packet size 8 bytes
        None,
    )

# @cocotb.test()
# def test_control_transfer_out_nak_data(dut):
#     harness = UsbTest(dut)
#     yield harness.reset()
#     yield harness.connect()

#     addr = 20
#     setup_data = [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00]
#     out_data = [
#         0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07,
#         0x08, 0x09, 0x0A, 0x0B,
#     ]

#     epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
#     yield harness.clear_pending(epaddr_out)

#     # Setup stage
#     # -----------
#     yield harness.transaction_setup(addr, setup_data)

#     # Data stage
#     # ----------
#     yield harness.set_response(epaddr_out, EndpointResponse.NAK)
#     yield harness.host_send_token_packet(PID.OUT, addr, epaddr_out)
#     yield harness.host_send_data_packet(PID.DATA1, out_data)
#     yield harness.host_expect_nak()

#     yield harness.host_send_token_packet(PID.OUT, addr, epaddr_out)
#     yield harness.host_send_data_packet(PID.DATA1, out_data)
#     yield harness.host_expect_nak()

#     #for i in range(200):
#     #    yield

#     yield harness.set_response(epaddr_out, EndpointResponse.ACK)
#     yield harness.host_send_token_packet(PID.OUT, addr, epaddr_out)
#     yield harness.host_send_data_packet(PID.DATA1, out_data)
#     yield harness.host_expect_ack()
#     yield harness.host_expect_data(epaddr_out, out_data)
#     yield harness.clear_pending(epaddr_out)

@cocotb.test()
def test_in_transfer(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    addr = 28
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
    yield harness.host_expect_data_packet(PID.DATA0, d[4:])
    yield harness.host_send_ack()

@cocotb.test()
def test_in_transfer_stuff_last(dut):
    harness = UsbTest(dut)
    yield harness.reset()
    yield harness.connect()

    addr = 28
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

    addr = 28
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

    addr = 28
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