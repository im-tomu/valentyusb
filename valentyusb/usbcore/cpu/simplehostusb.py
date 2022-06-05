#!/usr/bin/env python3

from enum import IntEnum

from migen import *
from migen.genlib import fifo
from migen.genlib.cdc import MultiReg, PulseSynchronizer, BusSynchronizer, BlindTransfer

from litex.soc.integration.doc import AutoDoc, ModuleDoc
from litex.soc.interconnect import stream
from litex.soc.interconnect import csr_eventmanager as ev
from litex.soc.interconnect.csr import CSRStorage, CSRStatus, CSRField, AutoCSR

from litex.soc.cores.gpio import GPIOOut

from ..endpoint import EndpointType, EndpointResponse
from ..pid import PID, PIDTypes
from ..sm.hosttransfer import UsbHostTransfer
from ..rx.pullup_detect import RxPullUpDetect

"""
Register Interface:

"""

class SimpleHostUsb(Module, AutoCSR, AutoDoc):
    """
    """

    def __init__(self, iobuf, cdc=False):
        # USB Core
        self.submodules.usb_core = usb_core = UsbHostTransfer(iobuf, cdc=cdc, low_speed_support=True)
        self.iobuf = iobuf = usb_core.iobuf

        ems = []

        self.submodules.sof = sof = SOFHandler(usb_core, cdc=cdc)
        ems.append(sof.ev)

        self.submodules.transfer = transfer = TransferHandler(usb_core, cdc=cdc)
        ems.append(transfer.ev)

        self.submodules.pullup_detect = pullup_detect = PullUpDetector(usb_core, cdc=cdc)
        ems.append(pullup_detect.ev)

        self.submodules.ev = ev.SharedIRQ(*ems)

        self.ctrl = CSRStorage(
            fields=[CSRField("reset", 1, description="Set to ``1`` to reset the USB bus."),
                    CSRField("low_speed", 1, description="Set to ``1`` to switch root port to low speed mode."),
                    CSRField("sof_enable", 1, description="Set to ``1`` to enable transmission of SOF packets.")])

        sof_enabled = Signal()

        if cdc:
            self.specials += [
                MultiReg(self.ctrl.fields.reset, self.usb_core.i_reset, odomain="usb_12"),
                MultiReg(self.ctrl.fields.low_speed, self.usb_core.i_low_speed, odomain="usb_12"),
                MultiReg(self.ctrl.fields.sof_enable, sof_enabled, odomain="usb_12")
            ]
        else:
            self.comb += [
                self.crc_core.i_reset.eq(self.ctrl.fields.reset),
                self.crc_core.i_low_speed.eq(self.ctrl.fields.low_speed),
                sof_enabled.eq(self.ctrl.fields.sof_enable)
            ]

        self.comb += [
            transfer.data_out_advance.eq(usb_core.data_send_get),
            usb_core.data_send_have.eq(transfer.data_out_have),
            usb_core.data_send_payload.eq(transfer.data_out),
            transfer.data_recv_payload.eq(usb_core.data_recv_payload),
            transfer.data_recv_put.eq(usb_core.data_recv_put),
        ]

        self.comb += [
            usb_core.i_frame.eq(sof.frame),
            usb_core.i_sof.eq(sof.sof_pulse & sof_enabled),
            usb_core.i_addr.eq(transfer.command.addr),
            usb_core.i_ep.eq(transfer.command.epno),
            usb_core.i_cmd_setup.eq(transfer.command.setup),
            usb_core.i_cmd_in.eq(getattr(transfer.command, 'in')),
            usb_core.i_cmd_out.eq(transfer.command.out),
            usb_core.i_cmd_pre.eq(transfer.command.pre),
            usb_core.i_cmd_data1.eq(transfer.command.data1),
            usb_core.i_cmd_iso.eq(transfer.command.iso),
        ]

class SOFHandler(Module, AutoCSR):

    def __init__(self, usb_core, cdc=False):
        self.frame = frame = Signal(11)
        counter = Signal(max=12000)
        self.sof_pulse = new_frame = Signal()
        self.sync.usb_12 += [
            If (counter == 11999,
                counter.eq(0),
                frame.eq(frame+1),
                new_frame.eq(1)
            ).Else(counter.eq(counter+1),
                   new_frame.eq(0))
        ]

        self.frame_csr = CSRStatus(name="frame",
            fields=[
                CSRField("frame", 11, description="Current USB frame number"),
            ])

        self.submodules.ev = ev.EventManager()
        self.ev.submodules.new_frame = ev.EventSourcePulse(name="new_frame")
        self.ev.finalize()

        if cdc:
            self.submodules.frame_blind = blind = BlindTransfer("usb_12", "sys", 11)
            self.comb += [
                blind.i.eq(new_frame),
                blind.data_i.eq(frame),
                self.ev.new_frame.trigger.eq(blind.o),
                self.frame_csr.fields.frame.eq(blind.data_o)
            ]
        else:
            self.comb += [
                self.ev.new_frame.trigger.eq(new_frame),
                self.frame_csr.fields.frame.eq(frame)
            ]


class TransferHandler(Module, AutoCSR):

    def __init__(self, usb_core, cdc=False):
        if cdc:
            self.submodules.write_data_buf = write_buf = ResetInserter(["usb_12", "sys"])(ClockDomainsRenamer({"write":"sys","read":"usb_12"})(fifo.AsyncFIFOBuffered(width=8, depth=64)))
            self.submodules.read_data_buf = read_buf = ResetInserter(["usb_12", "sys"])(ClockDomainsRenamer({"write":"usb_12","read":"sys"})(fifo.AsyncFIFOBuffered(width=8, depth=128))) # 66
        else:
            self.submodules.write_data_buf = write_buf = ResetInserter()(fifo.SyncFIFOBuffered(width=8, depth=64))
            self.submodules.read_data_buf = read_buf = ResetInserter()(fifo.SyncFIFOBuffered(width=8, depth=128)) # 66

        self.data_out_fifo = CSRStorage(name="data_out",
            fields=[
                CSRField("data", 8, description="The next byte to add to the transmit FIFO."),
            ],
            description="""
                Each byte written into this register gets added to an outgoing FIFO. Any
                bytes that are written here will be transmitted in the order in which
                they were added.  The FIFO queue is automatically advanced with each write.
                The FIFO queue is 64 bytes deep.  If you exceed this amount, the result is undefined."""
        )

        self.data_in_fifo = CSRStatus(name="data_in",
            fields=[
                CSRField("data", 8, description="The top byte of the receive FIFO."),
            ],
            description="""
                Data received from the device will go into a FIFO.  This register
                reflects the contents of the top byte in that FIFO.  Reading from
                this register advances the FIFO pointer."""
        )

        self.cmd = cmd = CSRStorage(
            fields=[
                CSRField("addr", 7, description="The device address for the transaction."),
                CSRField("epno", 4, offset=8, description="The endpoint number for the transaction."),
                CSRField("setup", offset=16, description="Write a ``1`` here to start a SETUP transfer."),
                CSRField("in", description="Write a ``1`` here to start an IN transfer."),
                CSRField("out", description="Write a ``1`` here to start an OUT transfer."),
                CSRField("pre", description="Write a ``1`` here to send a PRE packet at the start of the transfer."),
                CSRField("data1", description="Write a ``1`` here to use a ``DATA1`` token for data, ``0`` for ``DATA0``."),
                CSRField("iso", description="Write a ``1`` here to skip handshake after data packet."),
            ],
            write_from_dev=True,
            description="""
                Starts transfers."""
        )
        cmd.dat_w.eq(cmd.storage & 0xffff) # Clear upper 16 bits on cmd_latched

        self.command = Record([(f.name, f.size) for f in cmd.fields.fields])

        self.status = CSRStatus(
            fields=[
                CSRField("have", description="``1`` if there is data in the receive FIFO."),
            ],
            description="""
                Status."""
        )

        self.submodules.ev = ev.EventManager()
        self.ev.submodules.got_ack = ev.EventSourcePulse(name="ack", description="Got an ``ACK`` packet")
        self.ev.submodules.got_nak = ev.EventSourcePulse(name="nak", description="Got a ``NAK`` packet")
        self.ev.submodules.got_stall = ev.EventSourcePulse(name="stall", description="Got a ``STALL`` packet")
        self.ev.submodules.got_data0 = ev.EventSourcePulse(name="data0", description="Got a ``DATA0`` packet")
        self.ev.submodules.got_data1 = ev.EventSourcePulse(name="data1", description="Got a ``DATA1`` packet")
        self.ev.finalize()

        self.data_out = Signal(8)
        self.data_out_have = Signal()
        self.data_out_advance = Signal()

        self.data_recv_payload = Signal(8)
        self.data_recv_put = Signal()

        if cdc:
            self.submodules.acksync = BlindTransfer('usb_12', 'sys')
            self.submodules.naksync = BlindTransfer('usb_12', 'sys')
            self.submodules.stallsync = BlindTransfer('usb_12', 'sys')
            self.submodules.data0sync = BlindTransfer('usb_12', 'sys')
            self.submodules.data1sync = BlindTransfer('usb_12', 'sys')
            self.comb += [
                self.acksync.i.eq(usb_core.o_got_ack),
                self.ev.got_ack.trigger.eq(self.acksync.o),
                self.naksync.i.eq(usb_core.o_got_nak),
                self.ev.got_nak.trigger.eq(self.naksync.o),
                self.stallsync.i.eq(usb_core.o_got_stall),
                self.ev.got_stall.trigger.eq(self.stallsync.o),
                self.data0sync.i.eq(usb_core.o_got_data0),
                self.ev.got_data0.trigger.eq(self.data0sync.o),
                self.data1sync.i.eq(usb_core.o_got_data1),
                self.ev.got_data1.trigger.eq(self.data1sync.o),
            ]

            self.comb += [
                self.data_out.eq(write_buf.dout),
                write_buf.re.eq(self.data_out_advance),
                self.data_out_have.eq(write_buf.readable),
                read_buf.we.eq(self.data_recv_put),
                read_buf.din.eq(self.data_recv_payload),
            ]
            self.comb += [
                write_buf.we.eq(self.data_out_fifo.re),
                write_buf.din.eq(self.data_out_fifo.storage),
                self.status.fields.have.eq(read_buf.readable),
                self.data_in_fifo.fields.data.eq(read_buf.dout),
                read_buf.re.eq(self.data_in_fifo.we)
            ]

            self.submodules.cmd_latched_s = PulseSynchronizer('usb_12', 'sys')
            self.comb += [
                self.cmd_latched_s.i.eq(usb_core.o_cmd_latched),
                cmd.we.eq(self.cmd_latched_s.o)
            ]
            self.submodules.cmd_s = BusSynchronizer(len(self.command), 'sys', 'usb_12')
            command_sys = Record(self.command.layout)
            self.comb += [
                self.cmd_s.i.eq(command_sys.raw_bits()),
                self.command.raw_bits().eq(self.cmd_s.o),
            ]
            self.comb += [
                getattr(command_sys, f.name).eq(
                    cmd.storage[f.offset:f.offset+f.size]
                ) for f in cmd.fields.fields
            ]
        else:
            self.comb += [
                self.ev.got_ack.trigger.eq(usb_core.o_got_ack),
                self.ev.got_nak.trigger.eq(usb_core.o_got_nak),
                self.ev.got_stall.trigger.eq(usb_core.o_got_stall),
                self.ev.got_data0.trigger.eq(usb_core.o_got_data0),
                self.ev.got_data1.trigger.eq(usb_core.o_got_data1),
            ]

            self.comb += [
                self.data_out.eq(write_buf.dout),
                self.data_out_have.eq(write_buf.readable),
                write_buf.re.eq(self.data_out_advance),
                write_buf.we.eq(self.data_out_fifo.re),
                write_buf.din.eq(self.data_out_fifo.storage),
                self.status.fields.have.eq(read_buf.readable),
                self.data_in_fifo.fields.data.eq(read_buf.dout),
                read_buf.re.eq(self.data_in_fifo.we),
                read_buf.we.eq(self.data_recv_put),
                read_buf.din.eq(self.data_recv_payload),
            ]

            self.comb += cmd.we.eq(usb_core.o_cmd_latched)
            self.comb += [
                getattr(self.command, f.name).eq(
                    cmd.storage[f.offset:f.offset+f.size]
                ) for f in cmd.fields.fields
            ]

class PullUpDetector(Module, AutoCSR):

    def __init__(self, usb_core, cdc=False):
        pullup_detect = RxPullUpDetect()
        self.submodules.pullup_detect = pullup_detect = ClockDomainsRenamer("usb_48")(pullup_detect)
        j_pullup_detect = Signal()
        k_pullup_detect = Signal()
        self.pullup = CSRStatus(
            fields=[
                CSRField("j", 1, description="``1`` if a pull-up to state J is detected"),
                CSRField("k", 1, description="``1`` if a pull-up to state K is detected")
            ])
        self.specials += [
            MultiReg(pullup_detect.o_j_pullup_detect, j_pullup_detect),
            MultiReg(pullup_detect.o_k_pullup_detect, k_pullup_detect),
        ]
        self.comb += [
            pullup_detect.i_d_p.eq(usb_core.iobuf.usb_p_rx),
            pullup_detect.i_d_n.eq(usb_core.iobuf.usb_n_rx),
            pullup_detect.i_tx_en.eq(usb_core.tx.o_oe)
        ]
        self.sync += [
            self.pullup.fields.j.eq(j_pullup_detect),
            self.pullup.fields.k.eq(k_pullup_detect),
        ]
        self.submodules.ev = ev.EventManager()
        self.ev.submodules.pullup_change = ev.EventSourcePulse(name="pullup_change")
        self.ev.finalize()
        self.comb += [
            self.ev.pullup_change.trigger.eq((self.pullup.fields.j ^ j_pullup_detect) |
                                             (self.pullup.fields.k ^ k_pullup_detect))
        ]
