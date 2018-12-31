#!/usr/bin/env python3

import unittest

from migen import *

from litex.soc.cores.gpio import GPIOOut

from .pid import PIDTypes
from .rx.pipeline import RxPipeline
from .tx.pipeline import TxPipeline
from .utils.packet import *


class TxPacketSend(Module):
    def __init__(self, tx):
        self.submodules.tx = tx

        self.i_pkt_start = Signal()
        self.o_pkt_end = Signal()

        self.i_pid = Signal(4)
        self.i_data_payload = Signal(8)
        self.i_data_ready = Signal()
        self.o_data_ack = Signal()

        self.submodules.fsm = fsm = FSM()
        fsm.act('IDLE',
            tx.i_oe.eq(0),
            If(self.i_pkt_start,
                NextState('SYNC'),
            ),
        )

        # Send the SYNC byte
        fsm.act('SYNC',
            tx.i_data_payload.eq(0b00000001),
            tx.i_oe.eq(1),
            If(tx.o_data_strobe,
                NextState('PID'),
            ),
        )

        # Send the PID byte
        fsm.act('PID',
            tx.i_data_payload.eq(Cat(self.i_pid, self.i_pid ^ 0b1111)),
            tx.i_oe.eq(1),
            If(tx.o_data_strobe,
                If(self.i_pid | PIDTypes.TYPE_MASK == PIDTypes.HANDSHAKE,
                    self.o_pkt_end.eq(1),
                    NextState('IDLE'),
                ).Elif(self.i_pid | PIDTypes.TYPE_MASK == PIDTypes.DATA,
                    NextState('DATA'),
                ).Else(
                    NextState('ERROR'),
                ),
            ),
        )

        # Keep transmitting data bytes until the i_data_ready signal is not
        # high on a o_data_strobe event.
        fsm.act('DATA',
            tx.i_oe.eq(1),
            tx.i_data_payload.eq(self.i_data_payload),
            self.o_data_ack.eq(tx.o_data_strobe),
            If(tx.o_data_strobe,
                If(self.i_data_ready,
                    self.o_data_strobe.eq(1),
                ).Else(
                    NextState('CRC0'),
                ),
            ),
        )

        fsm.act('CRC0',
            tx.i_data_payload.eq(),
            If(tx.o_data_strobe,
                NextState('CRC1'),
            ),
        )
        fsm.act('CRC0',
            tx.i_data_payload.eq(),
            If(tx.o_data_strobe,
                self.o_pkt_end.eq(1),
                NextState('IDLE'),
            ),
        )

        fsm.act('ERROR')


class TestTxPacketSend(unittest.TestCase):

    def sim(self, stim):
        tx = RxPipeline()
        dut = TxPacketSend(tx)

        run_simulation(
            dut, stim(dut),
            vcd_name="vcd/test_token_decode_%s.vcd" % self.id(),
            clocks={"sys": 10, "usb_48": 40, "usb_12": 160},
        )

    def send_packet(self, pid, data=None):
        assert PIDTypes.handshake(pid) or PIDTypes.data(pid), pid

        yield dut.i_pid.eq(pid)


        yield dut.i_transmit.eq(1)
        if PIDTypes.handshake(pid):
            yield from self.wait_for_packet(dut, wrap_packet(handshake_packet(pid)))
        elif PIDTypes.data(pid):
            def tick_data():
                yield dut.i_data_ready.eq(len(data) > 0)
                yield dut.i_data_payload.eq(data[0])

                ack = yield dut.o_data_ack
                if ack:
                    data.pop(0)
            yield from self.wait_for_packet(dut, wrap_packet(data_packet(pid)), tick_data)

    def wait_for_packet(self, dut, bits, tick_data=None):
        clk12 = ClockSignal("usb_12")
        clk48 = ClockSignal("usb_48")

        def clk12_edge():
            if not tick_data:
                return
            yield from tick_data()

        usb = {
            'p': "",
            'n': "",
        }
        def clk48_edge(clk48=[0]):
            j = clk48[0]

            u = int(j/4)
            if u < len(oe):
                yield dut.i_oe.eq(int(oe[u]))

            if j % 4 == 0:
                yield dut.i_bit_strobe.eq(1)
            else:
                yield dut.i_bit_strobe.eq(0)

            usb['p'] += str((yield dut.o_usbp))
            usb['n'] += str((yield dut.o_usbn))

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
            i = 0
            N = 4*8

        while usb['p'][PAD:][-N:] != '1'*(N) and i < 10000:
            yield from tick()
            i += 1

        #assert usbn[20:] == 'J'*20

        return usb['p'], usb['n']


if __name__ == "__main__":
    unittest.main()
