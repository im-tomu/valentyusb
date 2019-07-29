# Simple tests for an adder module
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge
from cocotb.result import TestFailure, ReturnValue

from valentyusb.usbcore.utils.packet import *
from valentyusb.usbcore.endpoint import *
from valentyusb.usbcore.pid import *

USB_PULLUP_OUT=0XE0004800
USB_SETUP_EV_STATUS=0XE0004804
USB_SETUP_EV_PENDING=0XE0004808
USB_SETUP_EV_ENABLE=0XE000480C
USB_SETUP_DATA=0XE0004810
USB_SETUP_STATUS=0XE0004814
USB_SETUP_CTRL=0XE0004818
USB_EPIN_EV_STATUS=0XE000481C
USB_EPIN_EV_PENDING=0XE0004820
USB_EPIN_EV_ENABLE=0XE0004824
USB_EPIN_DATA=0XE0004828
USB_EPIN_STATUS=0XE000482C
USB_EPIN_EPNO=0XE0004830
USB_EPOUT_EV_STATUS=0XE0004834
USB_EPOUT_EV_PENDING=0XE0004838
USB_EPOUT_EV_ENABLE=0XE000483C
USB_EPOUT_DATA=0XE0004840
USB_EPOUT_STATUS=0XE0004844
USB_EPOUT_CTRL=0XE0004848
USB_ENABLE=0XE000484C

@cocotb.coroutine
def wishbone_write(dut, addr, value):
    dut.wishbone_adr = addr>>2
    dut.wishbone_dat_w = value
    dut.wishbone_sel = 7
    dut.wishbone_cyc = 1
    dut.wishbone_stb = 1
    dut.wishbone_we = 1

    dut._log.info("ack: {}".format(dut.wishbone_ack))

    while int(dut.wishbone_ack.value) != 1:
        yield RisingEdge(dut.clk48)
    raise ReturnValue(0)

@cocotb.coroutine
def wishbone_read(dut, addr):
    dut.wishbone_adr = addr>>2
    dut.wishbone_sel = 7
    dut.wishbone_cyc = 1
    dut.wishbone_stb = 1
    dut.wishbone_we = 0
    while int(dut.wishbone_ack) != 1:
        yield RisingEdge(dut.clk48)
    raise ReturnValue(int(dut.wishbone_dat_r.value))

def init(dut):
    cocotb.fork(Clock(dut.clk48, int(20.83), 'ns').start())

# Host->Device
@cocotb.coroutine
def _send_packet(dut, packet):
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
            dut.usb_d_p = 0
            dut.usb_d_n = 0
        elif v == '1':
            # SE1 - illegal, should never occur
            dut.usb_d_p = 1
            dut.usb_d_n = 1
        elif v == '-' or v == 'I':
            # Idle
            dut.usb_d_p = 1
            dut.usb_d_n = 0
        elif v == 'J':
            dut.usb_d_p = 1
            dut.usb_d_n = 0
        elif v == 'K':
            dut.usb_d_p = 0
            dut.usb_d_n = 1
        else:
            assert False, "Unknown value: %s" % v
        yield RisingEdge(dut.clk48)
        yield RisingEdge(dut.clk48)
        yield RisingEdge(dut.clk48)
        yield RisingEdge(dut.clk48)
    raise ReturnValue(0)

@cocotb.coroutine
def send_token_packet(dut, pid, addr, epaddr):
    epnum = EndpointType.epnum(epaddr)
    yield _send_packet(dut, token_packet(pid, addr, epnum))
    raise ReturnValue(0)

@cocotb.coroutine
def send_data_packet(dut, pid, data):
    assert pid in (PID.DATA0, PID.DATA1), pid
    yield _send_packet(dut, data_packet(pid, data))
    raise ReturnValue(0)

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

@cocotb.coroutine
def expect_setup(dut, epaddr, expected_data):
    actual_data = []
    for i in range(48):
        dut._log.info("Loop {}".format(i))
        yield wishbone_write(dut, USB_SETUP_CTRL, 1)
        status = yield wishbone_read(dut, USB_SETUP_STATUS)
        have = status & 1
        if not have:
            break
        v = yield wishbone_read(dut, USB_SETUP_DATA)
        actual_data.append(v)
        yield RisingEdge(dut.clk48)

    if len(actual_data) < 2:
        dut.raise_error("data {} was short", actual_data)
    actual_data, actual_crc16 = actual_data[:-2], actual_data[-2:]

    ep_print(epaddr, "Got: %r (expected: %r)", actual_data, expected_data)
    raise ReturnValue(0)
    # self.assertSequenceEqual(expected_data, actual_data)
    # self.assertSequenceEqual(crc16(expected_data), actual_crc16)

@cocotb.coroutine
def transaction_setup(dut, addr, data):
    epaddr_out = EndpointType.epaddr(0, EndpointType.OUT)
    epaddr_in = EndpointType.epaddr(0, EndpointType.IN)

    yield send_token_packet(dut, PID.SETUP, addr, epaddr_out)
    yield send_data_packet(dut, PID.DATA0, data)
    yield expect_setup(dut, epaddr_out, data)
    raise ReturnValue(0)
    # yield from self.clear_pending(epaddr_out)

    # # Check nothing pending at the end
    # self.assertFalse((yield from self.pending(epaddr_out)))

    # # Check the token is set correctly
    # yield from self.expect_last_tok(epaddr_out, 0b11)

    # # Check the in/out endpoint is reset to NAK
    # self.assertEqual((yield from self.response(epaddr_out)), EndpointResponse.NAK)
    # self.assertEqual((yield from self.response(epaddr_in)), EndpointResponse.NAK)

@cocotb.test()
def iobuf_validate(dut):
    init(dut)
    
    val = yield wishbone_read(dut, USB_PULLUP_OUT)
    if dut.usb_pullup != 0:
        raise TestFailure("USB pullup is not zero")

    yield wishbone_write(dut, USB_PULLUP_OUT, 1)
    yield RisingEdge(dut.clk48)

    val = yield wishbone_read(dut, USB_PULLUP_OUT)
    dut._log.info("Memory value: {}".format(val))
    if val != 1:
        raise TestFailure("USB pullup is not set!")

@cocotb.test()
def test_control_setup(dut):
    init(dut)
    #   012345   0123
    # 0b011100 0b1000
    yield transaction_setup(dut, 28, [0x80, 0x06, 0x00, 0x06, 0x00, 0x00, 0x0A, 0x00])
