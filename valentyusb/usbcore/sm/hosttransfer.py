#!/usr/bin/env python3

from migen import *
from migen.genlib import fsm
from migen.genlib.cdc import MultiReg

from ..endpoint import EndpointType, EndpointResponse
from ..pid import PID, PIDTypes
from ..rx.pipeline import RxPipeline
from ..tx.pipeline import TxPipeline
from ..sm.header import PacketHeaderDecode
from ..sm.send import TxPacketSend

class UsbHostTransfer(Module):

    def __init__(self, iobuf, auto_crc=True, cdc=False, low_speed_support=False):
        self.submodules.iobuf = iobuf = ClockDomainsRenamer("usb_48")(iobuf)
        if iobuf.usb_pullup is not None:
            self.comb += iobuf.usb_pullup.eq(0)
        self.submodules.tx = tx = TxPipeline(low_speed_support=low_speed_support)
        self.submodules.txstate = txstate = TxPacketSend(tx, auto_crc=auto_crc, token_support=True)
        self.submodules.rx = rx = RxPipeline(low_speed_support=low_speed_support)
        self.submodules.rxstate = rxstate = PacketHeaderDecode(rx)
        self.comb += [
            tx.i_bit_strobe.eq(rx.o_bit_strobe),
        ]

        self.data_recv_put = Signal()
        self.data_recv_payload = Signal(8)

        self.data_send_get = Signal()
        self.data_send_have = Signal()
        self.data_send_payload = Signal(8)
        self.data_end = Signal()

        self.i_addr = Signal(7)
        self.i_ep = Signal(4)
        self.i_frame = Signal(11)
        self.comb += [
            txstate.i_addr.eq(self.i_addr),
            txstate.i_ep.eq(self.i_ep),
            txstate.i_frame.eq(self.i_frame)
        ]

        self.i_reset = Signal()
        self.i_sof = Signal()

        self.i_cmd_setup = Signal()
        self.i_cmd_in = Signal()
        self.i_cmd_out = Signal()
        self.i_cmd_pre = Signal()
        self.i_cmd_data1 = Signal()
        self.i_cmd_iso = Signal()
        self.o_cmd_latched = Signal()

        self.o_got_ack = Signal()
        self.o_got_nak = Signal()
        self.o_got_stall = Signal()
        self.o_got_data0 = Signal()
        self.o_got_data1 = Signal()

        cmd_data1 = Signal()
        cmd_iso = Signal()
        low_speed_override = Signal()
        sof_latch = Signal()

        reset_out = Signal()
        self.specials += MultiReg(self.i_reset, reset_out, odomain="usb_48")

        if low_speed_support:
            self.i_low_speed = Signal()
            low_speed = Signal()
            self.comb += [
                low_speed.eq(self.i_low_speed | low_speed_override),
                rx.i_low_speed.eq(low_speed),
                tx.i_low_speed.eq(low_speed),
            ]
        else:
            low_speed = 0

        self.comb += [
            rx.i_usbp.eq(iobuf.usb_p_rx),
            rx.i_usbn.eq(iobuf.usb_n_rx),
            iobuf.usb_ls_rx.eq(low_speed),
            iobuf.usb_tx_en.eq(tx.o_oe | reset_out),
            iobuf.usb_p_tx.eq(tx.o_usbp & ~reset_out),
            iobuf.usb_n_tx.eq(tx.o_usbn & ~reset_out),
        ]

        self.sync.usb_12 += If(self.i_sof, sof_latch.eq(1))

        fsm = ResetInserter()(FSM(reset_state='IDLE'))
        self.submodules.fsm = fsm = ClockDomainsRenamer('usb_12')(fsm)
        self.comb += fsm.reset.eq(self.i_reset | rx.o_reset)

        fsm.act('IDLE',
                NextValue(low_speed_override, 0),
                If (sof_latch,
                    If (low_speed,
                        NextState('KA' if low_speed_support else 'SOF')
                    ).Else (NextState('SOF'))
                ).Elif (self.i_cmd_setup | self.i_cmd_in | self.i_cmd_out,
                   If (self.i_cmd_pre,
                       NextState('PREAMBLE')
                   ).Else (NextState('START_TRANSFER'))))

        fsm.act('SOF',
                txstate.i_pkt_start.eq(1),
                txstate.i_pid.eq(PID.SOF),
                If (txstate.o_pkt_end,
                    NextValue(sof_latch, 0),
                    NextState('IDLE')))

        fsm.act('PREAMBLE',
                txstate.i_pkt_start.eq(1),
                txstate.i_pid.eq(PID.PRE),
                If (txstate.o_pkt_end,
                    NextValue(low_speed_override, 1),
                    NextState('START_TRANSFER_AFTER_PREAMBLE')))

        fsm.delayed_enter('START_TRANSFER_AFTER_PREAMBLE', 'START_TRANSFER', 4)

        fsm.act('START_TRANSFER',
                self.o_cmd_latched.eq(1),
                NextValue(cmd_data1, self.i_cmd_data1),
                NextValue(cmd_iso, self.i_cmd_iso),
                If (self.i_cmd_setup,
                        NextState('SETUP')
                ).Elif (self.i_cmd_in,
                        NextState('IN')
                ).Elif (self.i_cmd_out,
                        NextState('OUT')
                ).Else (NextState('IDLE')))

        fsm.act('SETUP',
                txstate.i_pkt_start.eq(1),
                txstate.i_pid.eq(PID.SETUP),
                If (txstate.o_pkt_end,
                    NextState('SEND_DATA')))

        fsm.act('IN',
                txstate.i_pkt_start.eq(1),
                txstate.i_pid.eq(PID.IN),
                If (txstate.o_pkt_end,
                    NextState('WAIT_REPLY')))

        fsm.act('OUT',
                txstate.i_pkt_start.eq(1),
                txstate.i_pid.eq(PID.OUT),
                If (txstate.o_pkt_end,
                    NextState('SEND_DATA')))

        fsm.act('SEND_DATA',
                txstate.i_pkt_start.eq(1),
                txstate.i_pid.eq(Mux(cmd_data1, PID.DATA1, PID.DATA0)),
                self.data_send_get.eq(txstate.o_data_ack),
                self.data_end.eq(txstate.o_pkt_end),
                If (txstate.o_pkt_end,
                    If(cmd_iso,
                       NextState('IDLE')
                    ).Else(NextState('WAIT_REPLY'))))

        fsm.act('WAIT_REPLY',
                If (rxstate.o_decoded,
                    If ((rxstate.o_pid & PIDTypes.TYPE_MASK) == PIDTypes.DATA,
                        NextState('RECV_DATA')
                    ).Else (NextState('IDLE'),
                            self.o_got_ack.eq(rxstate.o_pid == PID.ACK),
                            self.o_got_nak.eq(rxstate.o_pid == PID.NAK),
                            self.o_got_stall.eq(rxstate.o_pid == PID.STALL))
                ).Elif(self.i_cmd_setup | self.i_cmd_in | self.i_cmd_out,
                       NextValue(low_speed_override, 0),
                       If (self.i_cmd_pre,
                           NextState('PREAMBLE')
                       ).Else (NextState('START_TRANSFER'))))

        fsm.act('RECV_DATA',
                self.data_recv_put.eq(rx.o_data_strobe),
                If(rx.o_pkt_end,
                   If (True,
                      NextState('END_DATA_LS')
                   ).Else (NextState('END_DATA'))))

        fsm.delayed_enter('END_DATA_LS', 'END_DATA', 8)

        fsm.act('END_DATA',
                # FIXME: Discard if CRC16 is incorrect
                self.o_got_data0.eq(rxstate.o_pid == PID.DATA0),
                self.o_got_data1.eq(rxstate.o_pid == PID.DATA1),
                If (cmd_iso,
                    NextState('IDLE')
                ).Else (NextState('ACK')))

        fsm.act('ACK',
                txstate.i_pkt_start.eq(1),
                txstate.i_pid.eq(PID.ACK),
                If (txstate.o_pkt_end,
                    NextState('IDLE')))

        if low_speed_support:
            fsm.act('KA',
                    NextValue(tx.i_keepalive, 1),
                    NextState('KA_WAIT1'))
            fsm.delayed_enter('KA_WAIT1', 'KA_MID', 10)
            fsm.act('KA_MID',
                    NextValue(tx.i_keepalive, 0),
                    NextState('KA_WAIT2'))
            fsm.delayed_enter('KA_WAIT2', 'KA_END', 10)
            fsm.act('KA_END',
                    NextValue(sof_latch, 0),
                    NextState('IDLE'))

        self.comb += [
            self.data_recv_payload.eq(rx.o_data_payload),
            txstate.i_data_payload.eq(self.data_send_payload),
            txstate.i_data_ready.eq(self.data_send_have),
        ]
