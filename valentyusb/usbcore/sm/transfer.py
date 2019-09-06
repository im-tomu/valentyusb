#!/usr/bin/env python3

import unittest

from migen import *

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
        self.submodules.iobuf = ClockDomainsRenamer("usb_48")(iobuf)

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

        # Whether to reset the FSM
        self.reset = Signal()

        # The state of the USB reset (SE0) signal
        self.usb_reset = Signal()
        self.comb += self.usb_reset.eq(rx.o_reset)

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
        self.rdy  = Signal(reset=1)
        self.dtb  = Signal()
        self.arm  = Signal()
        self.sta  = Signal()
        self.addr = Signal(7)       # If the address doesn't match, we won't respond

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

        self.tok    = Signal(4)     # Contains the transfer token type
        self.endp   = Signal(4)

        self.idle   = Signal(reset=0)      # Asserted when in the "WAIT_TOKEN" state
        self.start  = Signal()      # Asserted when a transfer is starting
        self.poll   = Signal()      # Asserted when polling for a response (i.e. one cycle after `self.start`)
        self.setup  = Signal()      # Asserted when a transfer is a setup
        self.commit = Signal()      # Asserted when a transfer succeeds
        self.retry  = Signal()      # Asserted when the host sends an IN without an ACK
        self.abort  = Signal()      # Asserted when a transfer fails
        self.end    = Signal()      # Asserted when transfer ends
        self.data_end=Signal()      # Asserted when a DATAx transfer finishes
        self.error  = Signal()      # Asserted when in the ERROR state
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
        transfer = ResetInserter()(FSM(reset_state="WAIT_TOKEN"))
        self.submodules.transfer = transfer = ClockDomainsRenamer("usb_12")(transfer)
        self.comb += transfer.reset.eq(self.reset)
        transfer.act("ERROR",
            self.error.eq(1),
        )

        transfer.act("WAIT_TOKEN",
            self.idle.eq(1),
            If(rx.o_pkt_start,
                NextState("RECV_TOKEN"),
            ),
        )

        transfer.act("RECV_TOKEN",
            self.idle.eq(0),
            If(rxstate.o_decoded,
                # If the address doesn't match, go back and wait for
                # a new token.
                If(rxstate.o_addr != self.addr,
                    NextState("WAIT_TOKEN"),
                ).Else(
                    self.start.eq(1),
                    NextValue(self.tok,  rxstate.o_pid),
                    NextValue(self.endp, rxstate.o_endp),
                    NextState("POLL_RESPONSE"),
                ),
            ),
        )

        response_pid = Signal(4)
        transfer.act("POLL_RESPONSE",
            self.poll.eq(1),
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
            # If we've indicated that we'll accept the data, put it into
            # `data_recv_payload` and strobe `data_recv_put` every time
            # a full byte comes in.
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
            If(self.dtb,
                txstate.i_pid.eq(PID.DATA1),
            ).Else(
                txstate.i_pid.eq(PID.DATA0),
            ),
            self.data_send_get.eq(txstate.o_data_ack),
            self.data_end.eq(txstate.o_pkt_end),
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
                If(rxstate.o_pid == PID.ACK,
                    NextState("WAIT_TOKEN"),
                ).Elif(rxstate.o_pid == PID.IN,
                    self.retry.eq(1),
                    NextState("SEND_DATA"),
                ).Else(
                    NextState("ERROR"),
                )
            ),
        )
        transfer.act("SEND_HAND",
            txstate.i_pid.eq(response_pid),
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
            If(transfer.before_entering("SEND_DATA"),
                If(self.dtb,
                    txstate.i_pid.eq(PID.DATA1),
                ).Else(
                    txstate.i_pid.eq(PID.DATA0),
                ),
                txstate.i_pkt_start.eq(1),
            ),
            If(transfer.before_entering("SEND_HAND"),
                txstate.i_pid.eq(response_pid),
                txstate.i_pkt_start.eq(1),
            ),
        ]
