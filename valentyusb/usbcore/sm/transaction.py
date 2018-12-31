#!/usr/bin/env python3

import unittest

from migen import *

from litex.soc.cores.gpio import GPIOOut

from .pid import PIDTypes
from .rx.pipeline import RxPipeline
from .tx.pipeline import TxPipeline
from .utils.packet import *


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


class UsbState(Module):
    def __init__(self, iobuf):
        self.submodules.iobuf = iobuf


        tx_byte0 = Signal(8)
        tx_byte1 = Signal(8)

        tx_pid = Signal(4)
        tx_addr = Signal(7)
        tx_endp = Signal(4)
        tx_crc5 = Signal(5)

        self.comb += [
            tx_byte0.eq(Cat(tx_pid, tx_pid ^ 0b1111)),
            tx_byte1.eq(Cat(tx_endp[4], tx_addr)),
            tx_byte2.eq(Cat(tx_endp[0:3], tx_crc5)),
        ]

        self.submodules.tx = tx = TxPipeline()

        # ----------------------
        # USB 48MHz bit strobe
        # ----------------------
        self.comb += [
            tx.i_bit_strobe.eq(rx.o_bit_strobe),
        ]

        # ----------------------
        # Tristate
        # ----------------------
        self.submodules.iobuf = iobuf
        self.comb += [
            rx.i_usbp.eq(iobuf.usb_p_rx),
            rx.i_usbn.eq(iobuf.usb_n_rx),
            iobuf.usb_tx_en.eq(tx.o_oe),
            iobuf.usb_p_tx.eq(tx.o_usbp),
            iobuf.usb_n_tx.eq(tx.o_usbn),
        ]
        self.submodules.pullup = GPIOOut(iobuf.usb_pullup)

        self.transfer_tok    = Signal(4)    # Contains the transfer token type
        self.transfer_start  = Signal()     # Asserted when a transfer is starting
        self.transfer_setup  = Signal()     # Asserted when a transfer is a setup
        self.transfer_commit = Signal()     # Asserted when a transfer succeeds
        self.transfer_abort  = Signal()     # Asserted when a transfer fails
        self.transfer_end    = Signal()     # Asserted when transfer ends
        self.comb += [
            self.transfer_end.eq(self.transfer_commit | self.transfer_abort),
        ]

        self.dtb = Signal()
        self.arm = Signal()
        self.sta = Signal()


        # Host->Device data path (Out + Setup data path)
        #
        # Setup --------------------
        # >Setup
        # >Data0[bmRequestType, bRequest, wValue, wIndex, wLength]
        # <Ack
        # --------------------------
        #
        # Data ---------------------
        # >Out        >Out        >Out
        # >DataX[..]  >DataX[..]  >DataX
        # <Ack        <Nak        <Stall
        #
        # Status -------------------
        # >Out
        # >Data0[]
        # <Ack
        # ---------------------------
        #
        # Host<-Device data path (In data path)
        # --------------------------
        # >In         >In     >In
        # <DataX[..]  <Stall  <Nak
        # >Ack
        # ---------------------------
        # >In
        # <Data0[]
        # >Ack
        # ---------------------------
        self.submodules.transfer = transfer = FSM(reset_state="WAIT_TOKEN")
        transfer.act("ERROR",
            If(self.reset, NextState("WAIT_TOKEN")),
        )

        transfer.act("WAIT_TOKEN",
            If(self.rx.o_pkt_start, NextState("RECV_TOKEN")),
        )

        transfer.act("RECV_TOKEN",
            self.transfer_start.eq(1),
            If(pkt_end,
                NextValue(self.ep_addr, self.fast_ep_addr),
                NextValue(self.transfer_tok, self.rx.decode.o_pid),
                #If(self.rx.decode.o_addr != addr, NextState("IGNORE")),

                If(rx.decode.o_pid == PID.SETUP,
                    NextValue(response_pid, PID.ACK),
                ).Else(
                    If(self.sta,
                        NextValue(response_pid, PID.STALL),
                    ).Elif(self.arm,
                        NextValue(response_pid, PID.ACK),
                    ).Else(
                        NextValue(response_pid, PID.NAK),
                    ),
                ),

                # Setup transfer
                If(rx.decode.o_pid == PID.SETUP,
                    NextState("RECV_DATA"),

                # Out transfer
                ).Elif(rx.decode.o_pid == PID.OUT,
                    NextState("RECV_DATA"),

                # In transfer
                ).Elif(rx.decode.o_pid == PID.IN,
                    If(~self.arm,
                        NextState("SEND_HAND"),
                    ).Else(
                        NextState("SEND_DATA"),
                    ),
                ).Else(
                    NextState("WAIT_TOKEN"),
                ),
            ),
        )

        # Out + Setup pathway
        transfer.act("RECV_DATA",
            If(response_pid == PID.ACK,
                self.data_recv_put.eq(self.rx.decode.put_data),
            ),
            If(pkt_end, NextState("SEND_HAND")),
        )
        self.comb += [
            self.data_recv_payload.eq(self.rx.decode.data_n1),
        ]

        # In pathway
        transfer.act("SEND_DATA",
            self.data_send_get.eq(self.tx.o_data_get),
            If(pkt_end, NextState("RECV_HAND")),
        )
        self.comb += [
            self.tx.i_data_valid.eq(self.data_send_have),
            self.tx.i_data_payload.eq(self.data_send_payload),
        ]

        # Handshake
        transfer.act("RECV_HAND",
            # Host can't reject?
            self.transfer_commit.eq(1),
            If(pkt_end, NextState("WAIT_TOKEN")),
        )
        transfer.act("SEND_HAND",
            self.transfer_setup.eq(self.transfer_tok == (PID.SETUP >> 2)),
            If(response_pid == PID.ACK,
                self.transfer_commit.eq(1),
            ).Else(
                self.transfer_abort.eq(1),
            ),
            If(pkt_end, NextState("WAIT_TOKEN")),
        )

        # Code to initiate the sending of packets when entering the SEND_XXX
        # states.
        self.comb += [
            If(transfer.after_entering("SEND_DATA"),
                If(self.dtb,
                    self.tx.i_pid.eq(PID.DATA1),
                ).Else(
                    self.tx.i_pid.eq(PID.DATA0),
                ),
                self.tx.i_pkt_start.eq(1),
            ),
            If(transfer.after_entering("SEND_HAND"),
                self.tx.i_pid.eq(response_pid),
                self.tx.i_pkt_start.eq(1),
            ),
        ]


if __name__ == "__main__":
    unittest.main()
