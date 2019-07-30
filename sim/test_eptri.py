# Simple tests for an adder module
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from cocotb.result import TestFailure, TestSuccess, ReturnValue

from valentyusb.usbcore.utils.packet import *
from valentyusb.usbcore.endpoint import *
from valentyusb.usbcore.pid import *

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

    # Host->Device
    @cocotb.coroutine
    def _send_packet(self, packet):
        """Send a USB packet."""
        packet = wrap_packet(packet)
        # self.assertEqual('J', packet[-1], "Packet didn't end in J: "+packet)

        # # FIXME: Horrible hack...
        # # Wait for 4 idle clock cycles before sending the packet..
        # yield from self.idle(4)

        # yield self.packet_h2d.eq(1)
        for v in packet:
            # tx_en = yield self.usb_tx_en
            # assert not tx_en, "Currently transmitting!"

            if v == '0' or v == '_':
                # SE0 - both lines pulled low
                self.dut.usb_d_p = 0
                self.dut.usb_d_n = 0
            elif v == '1':
                # SE1 - illegal, should never occur
                self.dut.usb_d_p = 1
                self.dut.usb_d_n = 1
            elif v == '-' or v == 'I':
                # Idle
                self.dut.usb_d_p = 1
                self.dut.usb_d_n = 0
            elif v == 'J':
                self.dut.usb_d_p = 1
                self.dut.usb_d_n = 0
            elif v == 'K':
                self.dut.usb_d_p = 0
                self.dut.usb_d_n = 1
            else:
                raise TestFailure("Unknown value: %s" % v)
            yield RisingEdge(self.dut.clk48)
        raise ReturnValue(0)

    @cocotb.coroutine
    def send_token_packet(self, pid, addr, epaddr):
        epnum = EndpointType.epnum(epaddr)
        yield self._send_packet(token_packet(pid, addr, epnum))

    @cocotb.coroutine
    def send_data_packet(self, pid, data):
        assert pid in (PID.DATA0, PID.DATA1), pid
        yield self._send_packet(data_packet(pid, data))

    @cocotb.coroutine
    def expect_setup(self, epaddr, expected_data):
        actual_data = []
        for i in range(48):
            self.dut._log.info("Loop {}".format(i))
            yield self.write(self.csrs['usb_setup_ctrl'], 1)
            status = yield self.read(self.csrs['usb_setup_status'])
            have = status & 1
            if not have:
                break
            v = yield self.read(self.csrs['usb_setup_data'])
            actual_data.append(v)
            yield RisingEdge(self.dut.clk48)

        if len(actual_data) < 2:
            raise TestFailure("data {} was short".format(actual_data))
        actual_data, actual_crc16 = actual_data[:-2], actual_data[-2:]

        ep_print(epaddr, "Got: %r (expected: %r)", actual_data, expected_data)
        # self.assertSequenceEqual(expected_data, actual_data)
        # self.assertSequenceEqual(crc16(expected_data), actual_crc16)

    @cocotb.coroutine
    def transaction_setup(self, addr, data):
        epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
        epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

        yield self.send_token_packet(PID.SETUP, addr, epaddr_out)
        yield self.send_data_packet(PID.DATA0, data)
        yield self.expect_setup(epaddr_out, data)
        # yield from self.clear_pending(epaddr_out)

        # # Check nothing pending at the end
        # self.assertFalse((yield from self.pending(epaddr_out)))

        # # Check the token is set correctly
        # yield from self.expect_last_tok(epaddr_out, 0b11)

        # # Check the in/out endpoint is reset to NAK
        # self.assertEqual((yield from self.response(epaddr_out)), EndpointResponse.NAK)
        # self.assertEqual((yield from self.response(epaddr_in)), EndpointResponse.NAK)


# Device->Host
@cocotb.coroutine
def expect_packet(dut, packet, msg=None):
    """Except to receive the following USB packet."""
    # yield self.packet_d2h.eq(1)

    # Wait for transmission to start
    # yield from self.dut.iobuf.recv('I')
    # tx = 0
    # bit_times = 0
    # for i in range(0, 100):
    #     yield from self.update_internal_signals()
    #     tx = yield self.dut.iobuf.usb_tx_en
    #     if tx:
    #         break
    #     yield from self.tick_usb48()
    #     bit_times = bit_times + 1
    # self.assertTrue(tx, "No packet started, "+msg)

    # # USB specifies that the turn-around time is 7.5 bit times for the device
    # bit_time_max = 12.5
    # bit_time_acceptable = 7.5
    # self.assertLessEqual(bit_times/4.0, bit_time_max,
    #     msg="Response came in {} bit times, which is more than {}".format(bit_times / 4.0, bit_time_max))
    # if (bit_times/4.0) > bit_time_acceptable:
    #     print("WARNING: Response came in {} bit times (> {})".format(bit_times / 4.0, bit_time_acceptable))

    # # Read in the transmission data
    # result = ""
    # for i in range(0, 512):
    #     yield from self.update_internal_signals()

    #     result += yield from self.iobuf.current()
    #     yield from self.tick_usb48()
    #     tx = yield self.dut.iobuf.usb_tx_en
    #     if not tx:
    #         break
    # self.assertFalse(tx, "Packet didn't finish, "+msg)
    # yield self.packet_d2h.eq(0)

    # # FIXME: Get the tx_en back into the USB12 clock domain...
    # # 4 * 12MHz == Number of 48MHz ticks
    # for i in range(0, 4):
    #     yield from self.tick_usb12()

    # # Check the packet received matches
    # expected = pp_packet(wrap_packet(packet))
    # actual = pp_packet(result)
    # self.assertMultiLineEqualSideBySide(expected, actual, msg)
    raise ReturnValue(0)

def ep_print(epaddr, msg, *args):
    print("ep(%i, %s): %s" % (
        EndpointType.epnum(epaddr),
        EndpointType.epdir(epaddr).name,
        msg) % args)

@cocotb.test()
def iobuf_validate(dut):
    """Sanity test that the Wishbone bus actually works"""
    harness = UsbTest(dut)
    harness.wb.log.setLevel(logging.DEBUG)

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
    #   012345   0123
    # 0b011100 0b1000
    yield harness.transaction_setup(28, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])
