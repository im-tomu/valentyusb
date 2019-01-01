#!/usr/bin/env python3

import unittest

from migen import *

from litex.soc.cores.gpio import GPIOOut

from ..pid import PIDTypes
from ..tx.pipeline import TxPipeline
from ..tx.crc import TxCrcPipeline
from ..utils.asserts import assertMultiLineEqualSideBySide
from ..utils.packet import *
from ..utils.pprint import pp_packet


class TxPacketSend(Module):
    def __init__(self, tx, auto_crc=True):
        self.submodules.tx = tx

        self.i_pkt_start = Signal()
        self.o_pkt_end = Signal()

        self.i_pid = Signal(4)
        self.i_data_payload = Signal(8)
        self.i_data_ready = Signal()
        self.o_data_ack = Signal()

        self.submodules.fsm = fsm = ClockDomainsRenamer("usb_12")(FSM())
        fsm.act('IDLE',
            #NextValue(tx.i_oe, self.i_pkt_start),
            NextValue(tx.i_oe, 0),
            If(self.i_pkt_start,
                NextState('QUEUE_SYNC'),
            ),
        )

        # Send the QUEUE_SYNC byte
        fsm.act('QUEUE_SYNC',
            NextValue(tx.i_oe, 1),
            tx.i_data_payload[::-1].eq(0b00000001),
            If(tx.o_data_strobe,
                NextState('QUEUE_PID'),
            ),
        )

        # Send the PID byte
        fsm.act('QUEUE_PID',
            tx.i_data_payload.eq(Cat(self.i_pid, self.i_pid ^ 0b1111)),
            If(tx.o_data_strobe,
                If(self.i_pid & PIDTypes.TYPE_MASK == PIDTypes.HANDSHAKE,
                    NextState('WAIT_TRANSMIT'),
                ).Elif(self.i_pid & PIDTypes.TYPE_MASK == PIDTypes.DATA,
                    NextState('QUEUE_DATA'),
                ).Else(
                    NextState('ERROR'),
                ),
            ),
        )

        if not auto_crc:
            fsm.act('QUEUE_DATA',
                If(~self.i_data_ready,
                    NextState('WAIT_TRANSMIT'),
                ).Else(
                    NextState('QUEUE_DATA0'),
                ),
            )

            # Keep transmitting data bytes until the i_data_ready signal is not
            # high on a o_data_strobe event.
            fsm.act('QUEUE_DATA0',
                tx.i_data_payload.eq(self.i_data_payload),
                self.o_data_ack.eq(tx.o_data_strobe),
                If(tx.o_data_strobe & ~self.i_data_ready,
                    NextState('WAIT_TRANSMIT'),
                ),
            )
        else:
            crc = TxCrcPipeline()
            self.submodules.crc = crc = ClockDomainsRenamer("usb_12")(crc)

            last_data_payload = Signal(8)
            last_data_ready = Signal()

            self.comb += [
                crc.i_data_payload.eq(self.i_data_payload),
            ]
            self.sync += [
                If(fsm.ongoing('QUEUE_SYNC'),
                    crc.ce.eq(1),
                    crc.reset.eq(1),
                ),
                If(fsm.ongoing('QUEUE_PID'),
                    last_data_payload.eq(self.i_data_payload),
                    last_data_ready.eq(self.i_data_ready),
                    If(self.i_data_ready,
                        crc.reset.eq(0),
                    ),
                ),
            ]

            fsm.act('QUEUE_DATA',
                If(~self.i_data_ready,
                    NextState('QUEUE_CRC0'),
                ).Else(
                    NextState('QUEUE_DATA0'),
                ),
            )
            fsm.act('QUEUE_DATA0',
                If(crc.o_data_ack,
                    NextValue(last_data_payload, self.i_data_payload),
                    NextValue(last_data_ready, self.i_data_ready),
                    If(self.i_data_ready,
                        self.o_data_ack.eq(1),
                    ).Else(
                        NextValue(crc.ce, 0),
                    )
                ),
                tx.i_data_payload.eq(last_data_payload),
                If(tx.o_data_strobe & ~last_data_ready,
                    NextState('QUEUE_CRC0'),
                ),
            )
            fsm.act('QUEUE_CRC0',
                tx.i_data_payload.eq(crc.o_crc16[:8]),
                If(tx.o_data_strobe,
                    NextState('QUEUE_CRC1'),
                ),
            )
            fsm.act('QUEUE_CRC1',
                tx.i_data_payload.eq(crc.o_crc16[8:]),
                If(tx.o_data_strobe,
                    NextState('WAIT_TRANSMIT'),
                ),
            )

        fsm.act('WAIT_TRANSMIT',
            NextValue(tx.i_oe, 0),
            If(~tx.o_oe,
                self.o_pkt_end.eq(1),
                NextState('IDLE'),
            ),
        )

        fsm.act('ERROR')


class CommonTxPacketSendTestCase(unittest.TestCase):
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
        print()
        print("---- ", self.id(), " ----", sep="")
        print(usb['p'])
        print(' '*(start-1), usb_p)
        print(' '*(start-1), usb_n)
        print(usb['n'])
        print("-----", len(self.id())*"-", "-----", sep="")
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

    def test_data1_all_zero(self):
        self.sim(PID.DATA1, data=[0, 0, 0, 0])

    def test_data0_descriptor(self):
        self.sim(PID.DATA0, data=[
            0x80, 0x06, 0x00, 0x01, 0x00, 0x00, 0x40, 0x00, 0xdd, 0x94,
        ])


class TestTxPacketSendNoCrc(CommonTxPacketSendTestCase):
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
            vcd_name="vcd/test_token_decode_%s.vcd" % self.id(),
            clocks={"sys": 10, "usb_48": 40, "usb_12": 160},
        )


class TestTxPacketSendAutoCrc(CommonTxPacketSendTestCase):
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
            vcd_name="vcd/test_token_decode_%s.vcd" % self.id(),
            clocks={"sys": 10, "usb_48": 40, "usb_12": 160},
        )




if __name__ == "__main__":
    unittest.main()
