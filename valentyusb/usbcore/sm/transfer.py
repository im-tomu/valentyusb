#!/usr/bin/env python3

import unittest

from migen import *

from litex.soc.cores.gpio import GPIOOut

from ..endpoint import *
from ..io import FakeIoBuf
from ..pid import PIDTypes
from ..rx.pipeline import RxPipeline
from ..tx.pipeline import TxPipeline
from .header import PacketHeaderDecode
from .send import TxPacketSend

from ..utils.packet import *


class UsbTransfer(Module):
    def __init__(self, iobuf, auto_crc=True):
        self.submodules.iobuf = iobuf

        self.submodules.tx = tx = TxPipeline()
        self.submodules.txstate = txstate = TxPacketSend(tx, auto_crc=auto_crc)

        self.submodules.rx = rx = RxPipeline()
        self.submodules.rxstate = rxstate = PacketHeaderDecode(rx)

        # ----------------------
        # USB 48MHz bit strobe
        # ----------------------
        self.comb += [
            tx.i_bit_strobe.eq(rx.o_bit_strobe),
        ]

        self.reset = Signal()

        # ----------------------
        # Data paths
        # ----------------------
        self.data_recv_put = Signal()
        self.data_recv_payload = Signal(8)

        self.data_send_get = Signal()
        self.data_send_have = Signal()
        self.data_send_payload = Signal(8)

        # ----------------------
        # State signally
        # ----------------------
        # The value of these signals are generally dependent on endp, so we
        # need to wait for the rdy signal to use them.
        self.rdy = Signal(reset=1)
        self.dtb = Signal()
        self.arm = Signal()
        self.sta = Signal()

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

        self.tok    = Signal(4)    # Contains the transfer token type
        self.addr   = Signal(7)
        self.endp   = Signal(4)

        self.start  = Signal()     # Asserted when a transfer is starting
        self.setup  = Signal()     # Asserted when a transfer is a setup
        self.commit = Signal()     # Asserted when a transfer succeeds
        self.abort  = Signal()     # Asserted when a transfer fails
        self.end    = Signal()     # Asserted when transfer ends
        self.comb += [
            self.end.eq(self.commit | self.abort),
        ]

        # Host->Device data path (Out + Setup data path)
        #
        # Token
        # Data
        # Handshake
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
        transfer = FSM(reset_state="WAIT_TOKEN")
        self.submodules.transfer = transfer = ClockDomainsRenamer("usb_12")(transfer)
        transfer.act("ERROR",
            If(self.reset, NextState("WAIT_TOKEN")),
        )

        transfer.act("WAIT_TOKEN",
            If(rx.o_pkt_start,
                NextState("RECV_TOKEN"),
            ),
        )

        transfer.act("RECV_TOKEN",
            If(rxstate.o_decoded,
                #If((rxstate.o_pid & PIDTypes.TYPE_MASK) != PIDTypes.TOKEN,
                #    NextState('ERROR'),
                #),
                NextValue(self.tok,  rxstate.o_pid),
                NextValue(self.addr, rxstate.o_addr),
                NextValue(self.endp, rxstate.o_endp),
                self.start.eq(1),
                NextState("POLL_RESPONSE"),
            ),
        )

        response_pid = Signal(4)
        transfer.act("POLL_RESPONSE",
            If(self.rdy,
                # Work out the response
                If(self.tok == PID.SETUP,
                    NextValue(response_pid, PID.ACK),
                ).Elif(self.sta,
                    NextValue(response_pid, PID.STALL),
                ).Elif(self.arm,
                    NextValue(response_pid, PID.ACK),
                ).Else(
                    NextValue(response_pid, PID.NAK),
                ),

                If(rxstate.o_pid == PID.SOF,
                    NextState("WAIT_TOKEN"),

                # Setup transfer
                ).Elif(self.tok == PID.SETUP,
                    NextState("WAIT_DATA"),

                # Out transfer
                ).Elif(self.tok == PID.OUT,
                    NextState("WAIT_DATA"),

                # In transfer
                ).Elif(self.tok == PID.IN,
                    If(~self.arm | self.sta,
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
        transfer.act("WAIT_DATA",
            If(rxstate.o_decoded,
                If((rxstate.o_pid & PIDTypes.TYPE_MASK) == PIDTypes.DATA,
                    NextState("RECV_DATA"),
                ).Elif(rxstate.o_pid == PID.SOF,
                    NextState("WAIT_DATA"),
                ).Else(
                    NextState("ERROR"),
                )
            ),
        )

        transfer.act("RECV_DATA",
            If(response_pid == PID.ACK,
                self.data_recv_put.eq(rx.o_data_strobe),
            ),
            If(rx.o_pkt_end,
                NextState("SEND_HAND"),
            ),
        )
        self.comb += [
            self.data_recv_payload.eq(rx.o_data_payload),
        ]

        # In pathway
        transfer.act("SEND_DATA",
            self.data_send_get.eq(txstate.o_data_ack),
            If(txstate.o_pkt_end, NextState("WAIT_HAND")),
        )
        self.comb += [
            txstate.i_data_payload.eq(self.data_send_payload),
            txstate.i_data_ready.eq(self.data_send_have),
        ]

        # Handshake
        transfer.act("WAIT_HAND",
            If(rxstate.o_decoded,
                self.commit.eq(1),
                # Host can't reject?
                If((rxstate.o_pid & PIDTypes.TYPE_MASK) == PIDTypes.HANDSHAKE,
                    NextState("WAIT_TOKEN"),
                ).Else(
                    NextState("ERROR"),
                )
            ),
        )
        transfer.act("SEND_HAND",
            # Do some pipelining.  Transmit the last byte of data
            # here as part of the handshake process.
            If(response_pid == PID.ACK,
                self.data_recv_put.eq(rx.o_data_strobe),
            ),
            If(txstate.o_pkt_end,
                self.setup.eq(self.tok == PID.SETUP),
                If(response_pid == PID.ACK,
                    self.commit.eq(1),
                ).Else(
                    self.abort.eq(1),
                ),
                NextState("WAIT_TOKEN"),
            ),
        )

        # Code to reset header decoder when entering the WAIT_XXX states.
        self.comb += [
            If(tx.o_oe,
                rx.reset.eq(1),
            ),
        ]
        # Code to initiate the sending of packets when entering the SEND_XXX
        # states.
        self.comb += [
            If(transfer.after_entering("SEND_DATA"),
                If(self.dtb,
                    txstate.i_pid.eq(PID.DATA1),
                ).Else(
                    txstate.i_pid.eq(PID.DATA0),
                ),
                txstate.i_pkt_start.eq(1),
            ),
            If(transfer.after_entering("SEND_HAND"),
                txstate.i_pid.eq(response_pid),
                txstate.i_pkt_start.eq(1),
            ),
        ]
