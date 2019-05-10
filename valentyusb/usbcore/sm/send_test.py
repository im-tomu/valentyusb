#!/usr/bin/env python3

import unittest

from migen import *

from ..pid import PIDTypes
from ..tx.pipeline import TxPipeline
from ..utils.asserts import assertMultiLineEqualSideBySide
from ..utils.packet import *
from ..utils.pprint import pp_packet
from ..test.common import BaseUsbTestCase

from .send import TxPacketSend


class CommonTxPacketSendTestCase:
    maxDiff=None

    def assert_packet_sent(self, dut, pid, data=None, ndata=None):
        assert PIDTypes.handshake(pid) or PIDTypes.data(pid), pid

        yield dut.i_pid.eq(pid)
        yield
        yield
        yield

        if PIDTypes.handshake(pid):
            expected_packet = wrap_packet(handshake_packet(pid))
            tick_data = None
        elif PIDTypes.data(pid):
            expected_packet = wrap_packet(data_packet(pid, data))
            def tick_data(last_ack=[False]):
                if len(ndata) > 0:
                    yield dut.i_data_payload.eq(ndata[0])

                    ack = yield dut.o_data_ack
                    if ack:
                        ndata.pop(0)
                    last_ack[0] = ack
                else:
                    yield dut.i_data_payload.eq(0)
                yield dut.i_data_ready.eq(len(ndata) > 0)

        actual_usb_p, actual_usb_n = yield from self.wait_for_packet(dut, tick_data)
        actual_packet = undiff(actual_usb_p, actual_usb_n)
        assertMultiLineEqualSideBySide(
            pp_packet(expected_packet),
            pp_packet(actual_packet),
            "%s packet (with data %r) send failed" % (pid, data),
        )

    def wait_for_packet(self, dut, tick_data=None):
        PAD_FRONT = 8
        PAD_BACK = 8
        N = 4

        clk12 = ClockSignal("usb_12")
        clk48 = ClockSignal("usb_48")

        def clk12_edge():
            start = yield dut.i_pkt_start
            if start:
                yield dut.i_pkt_start.eq(0)
            if not tick_data:
                return
            yield from tick_data()

        usb = {
            'p': "",
            'n': "",
        }
        def clk48_edge(clk48=[0]):
            j = clk48[0]

            if j % N == 0:
                yield dut.tx.i_bit_strobe.eq(1)
            else:
                yield dut.tx.i_bit_strobe.eq(0)

            usb['p'] += str((yield dut.tx.o_usbp))
            usb['n'] += str((yield dut.tx.o_usbn))

            clk48[0] += 1

        def tick(last={'clk12': None, 'clk48':None}):
            current_clk12 = yield clk12
            if current_clk12 and not last['clk12']:
                yield from clk12_edge()
            last['clk12'] = current_clk12

            current_clk48 = yield clk48
            if current_clk48 and not last['clk48']:
                yield from clk48_edge()
            last['clk48'] = current_clk48

            yield

        yield dut.i_pkt_start.eq(1)

        i = 0
        while usb['p'][PAD_FRONT*N:][-PAD_BACK*N:] != '1'*(PAD_BACK*N) and i < 10000:
            yield from tick()
            i += 1

        #assert usbn[20:] == 'J'*20
        start = usb['p'].find('0')
        end = usb['p'].rfind('0')+1+N
        usb_p = usb['p'][start:end]
        usb_n = usb['n'][start:end]
#        print()
#        print("---- ", self.id(), " ----", sep="")
#        print(usb['p'])
#        print(' '*(start-1), usb_p)
#        print(' '*(start-1), usb_n)
#        print(usb['n'])
#        print("-----", len(self.id())*"-", "-----", sep="")
        return usb_p, usb_n

    def test_ack(self):
        self.sim(PID.ACK)

    def test_nak(self):
        self.sim(PID.NAK)

    def test_stall(self):
        self.sim(PID.STALL)

    def test_status0(self):
        self.sim(PID.DATA0, [])

    def test_status1(self):
        self.sim(PID.DATA1, [])

    def test_data0_one_zero(self):
        self.sim(PID.DATA0, [0])

    def test_data0_one_one(self):
        self.sim(PID.DATA0, [1])

    def test_data0_one_one(self):
        self.sim(PID.DATA0, [0b10000001])

    def test_data0_two_ones(self):
        self.sim(PID.DATA0, [1, 1])

    def test_data0_two_ones(self):
        self.sim(PID.DATA0, [0, 1])

    def test_data0_edges(self):
        self.sim(PID.DATA0, data=[0b00000001, 0, 0, 0b10000000])

    def test_data0_all_zero(self):
        self.sim(PID.DATA0, data=[0, 0, 0, 0])

    def test_data0_dat1234(self):
        self.sim(PID.DATA0, data=[1, 2, 3, 4])

    def test_data1_all_zero(self):
        self.sim(PID.DATA1, data=[0, 0, 0, 0])

    def test_data0_descriptor(self):
        self.sim(PID.DATA0, data=[
            0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94,
        ])


class TestTxPacketSendNoCrc(BaseUsbTestCase, CommonTxPacketSendTestCase):
    maxDiff=None

    def sim(self, pid, data=None):
        tx = TxPipeline()
        dut = TxPacketSend(tx, auto_crc=False)

        if data is not None:
            ndata = data + crc16(data)
        else:
            ndata = None

        def stim(dut):
            yield from self.assert_packet_sent(dut, pid, data, ndata)

        run_simulation(
            dut, stim(dut),
            vcd_name=self.make_vcd_name(),
            clocks={"sys": 10, "usb_48": 40, "usb_12": 160},
        )


class TestTxPacketSendAutoCrc(BaseUsbTestCase, CommonTxPacketSendTestCase):
    maxDiff=None

    def sim(self, pid, data=None):
        tx = TxPipeline()
        dut = TxPacketSend(tx, auto_crc=True)

        if data is not None:
            ndata = list(data)
        else:
            ndata = None

        def stim(dut):
            yield from self.assert_packet_sent(dut, pid, data, ndata)

        run_simulation(
            dut, stim(dut),
            vcd_name=self.make_vcd_name(),
            clocks={"sys": 10, "usb_48": 40, "usb_12": 160},
        )




if __name__ == "__main__":
    unittest.main()
