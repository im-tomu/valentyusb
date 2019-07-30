#!/usr/bin/env python3

from enum import IntEnum

from migen import *
from migen.genlib import fifo
from migen.genlib import cdc

from litex.soc.interconnect import stream
from litex.soc.interconnect import wishbone
from litex.soc.interconnect import csr_eventmanager as ev
from litex.soc.interconnect.csr import *

from litex.soc.cores.gpio import GPIOOut

from ..endpoint import EndpointType, EndpointResponse
from ..pid import PID, PIDTypes
from ..sm.transfer import UsbTransfer
from .usbwishbonebridge import USBWishboneBridge

# Register Interface:
#
# pullup_out_read: Read the status of the USB "FS" pullup.
# pullup_out_write: Write the USB "FS" pullup state
#
# SETUP - Responding to a SETUP packet from the host
# setup_read: Read the contents of the last SETUP transaction
# setup_ack: Write a "1" here to advance the data_read fifo
# setup_empty: "0" if there is no SETUP data.
# setup_epno: The endpoint the SETUP packet was destined for
#
# EPOUT - Data from the host to this device
# epout_data_read: Read the contents of the last transaction on the EP0
# epout_data_ack: Write a "1" here to advance the data_read fifo
# epout_last_tok: Bits 2 and 3 of the last token, from the following table:
#    USB_PID_OUT   = 0
#    USB_PID_SOF   = 1
#    USB_PID_IN    = 2
#    USB_PID_SETUP = 3
# epout_epno: Which endpoint contained the last data
# epout_queued: A response is queued and has yet to be acknowledged by the host
#
# EPIN - Requests from the host to read data from this device
# epin_data_write: Write 8 bits to the EP0 queue
# epin_data_empty: Return 1 if the queue is empty
# epin_epno: Which endpoint the data is for.  You must write this byte to indicate data is ready to be sent.
# epin_queued: A response is queued and has yet to be acknowledged by the host
#
# ep_stall: a 32-bit field representing endpoitns to respond with STALL.


### Handle SETUP packets
###
### SETUP packets must always respond with ACK.  Sometimes, they are followed
### by DATA packets, but not always.
class SetupHandler(Module, AutoCSR):
    def __init__(self, usb_core):
        self.submodules.ev = ev.EventManager()
        self.ev.submodules.error = ev.EventSourcePulse()
        self.ev.submodules.packet = ev.EventSourcePulse()
        self.ev.finalize()
        self.trigger = self.ev.packet.trigger

        self.data_recv_payload = Signal()
        self.data_recv_put = Signal()

        self.submodules.data = buf = ClockDomainsRenamer({"write": "usb_12", "read": "sys"})(ResetInserter()(fifo.SyncFIFOBuffered(width=8, depth=10)))
        #w {
        #w   "reg_definition": {
        #w       "reg_name": "DATA",
        #w       "reg_description": "Data from the last SETUP packet.  It will be 10 bytes long, because it will include the CRC16.  This is a FIFO; use `DATA_ACK` to advance the queue.",
        #w       "reg": [
        #w           { "name": "DATA",   "bits": 8, "attr": "RO", "description": "The next byte of SETUP data" }
        #w       ]
        #w   }
        #w }
        self.data = CSR(8)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "STATUS",
        #w       "reg_description": "Status about the most recent SETUP packet, and the state of the FIFO.",
        #w       "reg": [
        #w           { "name": "HAVE",  "bits": 1, "attr": "RO", "description": "`1` if there is data in the FIFO." },
        #w           { "name": "EPNO",  "bits": 5, "attr": "RO", "description": "The destination endpoint for the most recent SETUP packet." },
        #w           {                  "bits": 2 }
        #w       ]
        #w   }
        #w }
        self.status = CSRStatus(6)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "CTRL",
        #w       "reg_description": "Controls for managing `SETUP` packets.",
        #w       "reg": [
        #w           { "name": "ADVANCE", "bits": 1, "attr": "WO", "description": "Write a `1` here to advance the `DATA` FIFO." },
        #w           {                    "bits": 7 }
        #w       ]
        #w   }
        #w }
        self.ctrl = CSRStorage(1)

        # How to respond to requests:
        #  - 0 - ACK
        #  - 1 - NAK
        # Since we must always ACK a SETUP packet, set this to 0.
        self.response = Signal()
        self.comb += self.response.eq(0),

        epno = Signal(5)

        # Wire up the `STATUS` register
        self.comb += self.status.status.eq(Cat(buf.readable, epno))

        # Wire up the "SETUP" endpoint.
        self.comb += [
            # Set the FIFO output to be the current buffer HEAD
            self.data.w.eq(buf.dout),

            # Advance the FIFO when anything is written to the control bit
            buf.re.eq(self.ctrl.re & self.ctrl.storage[0]),

            If(usb_core.tok == PID.SETUP,
                buf.din.eq(self.data_recv_payload),
                buf.we.eq(self.data_recv_put),
                self.ev.packet.trigger.eq(usb_core.commit),
            )
        ]

        # When we get the start of a SETUP packet, update the `epno` value.
        check_reset = Signal()
        self.sync.usb_12 += [
            check_reset.eq(usb_core.start),
            If(check_reset,
                If(usb_core.tok == PID.SETUP,
                    epno.eq(usb_core.endp),
                    buf.reset.eq(1),
                ).Else(
                    buf.reset.eq(0),
                )
            ).Else(
                buf.reset.eq(0),
            )
        ]


class InHandler(Module, AutoCSR):
    """Endpoint for Device->Host data.

    Reads from the buffer memory.
    Raises packet IRQ when packet has been sent.
    CPU writes to the head CSR to push data onto the FIFO.
    """
    def __init__(self, usb_core):
        self.submodules.ev = ev.EventManager()
        self.ev.submodules.error = ev.EventSourcePulse()
        self.ev.submodules.packet = ev.EventSourcePulse()
        self.ev.finalize()
        self.trigger = self.ev.packet.trigger
        self.dtb = Signal()

        dtb = Signal(16)

        self.submodules.data_buf = data = fifo.SyncFIFOBuffered(width=8, depth=128)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "DATA",
        #w       "reg_description": "Write data to this register.  It is a FIFO, so any bytes that are written here will be transmitted in-order.  The FIFO queue is automatically advanced. The FIFO queue is 64 bytes deep.  If you exceed this amount, the result is undefined.",
        #w       "reg": [
        #w           { "name": "DATA",   "bits": 8, "attr": "WO", "description": "The next byte to add to the queue." }
        #w       ]
        #w   }
        #w }
        self.data = CSRStorage(8)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "STATUS",
        #w       "reg_description": "Determine the status of the `IN` pathway.",
        #w       "reg": [
        #w           { "name": "EMPTY",   "bits": 1, "attr": "RO", "description": "This value is '1' if the FIFO is empty." },
        #w           { "name": "IDLE",    "bits": 1, "attr": "RO", "description": "This value is '1' if the packet has finished transmitting." },
        #w           {                    "bits": 6 }
        #w       ]
        #w   }
        #w }
        self.status = CSRStatus(2)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "EP",
        #w       "reg_description": "After writing data to the `data` register, update this register with the destination endpoint number.  Writing to this register queues the packet for transmission.",
        #w       "reg": [
        #w           { "name": "EP",   "bits": 4, "attr": "WO", "description": "The endpoint number for the transaction that is queued in the FIFO." }
        #w           {                 "bits": 4 }
        #w       ]
        #w   }
        #w }
        self.epno = CSRStorage(4)

        xxxx_readable = Signal()
        self.specials.crc_readable = cdc.MultiReg(data.readable, xxxx_readable)

        # How to respond to requests:
        #  - 0 - ACK
        #  - 1 - NAK
        self.response = Signal()

        # This value goes "1" when data is pending, and returns to "0" when it's done.
        queued = Signal()

        # Outgoing data will be placed on this signal
        self.data_out = Signal(8)

        # This is "1" if `data_out` contains data
        self.data_out_have = Signal()

        # Pulse this to advance the data output
        self.data_out_advance = Signal()

        self.comb += [
            # We will respond with "ACK" if the register matches the current endpoint umber
            If(usb_core.endp == self.epno.storage,
                self.response.eq(~queued)
            ).Else(
                self.response.eq(1)
            ),

            # Wire up the "status" register
            self.status.status.eq(
                Cat(~xxxx_readable, ~queued)
            ),

            self.dtb.eq(dtb >> usb_core.endp),

            self.data_out.eq(data.dout),
            self.data_out_have.eq(data.readable),
            data.re.eq(self.data_out_advance),
        ]

        self.sync += [
            # When the user updates the `epno` register, enable writing.
            If(self.epno.re,
                queued.eq(1)
            )
            # When the USB core finishes operating on this packet,
            # de-assert the queue flag
            .Elif(usb_core.commit,
                If(usb_core.tok == self.epno.storage,
                    queued.eq(0),

                    # Toggle the "DTB" line
                    dtb.eq(dtb ^ (1 << self.epno.storage)),
                )
            )
        ]

class OutHandler(Module, AutoCSR):
    def __init__(self, usb_core):
# EPOUT - Data from the host to this device
        self.submodules.ev = ev.EventManager()
        self.ev.submodules.error = ev.EventSourcePulse()
        self.ev.submodules.packet = ev.EventSourcePulse()
        self.ev.finalize()
        self.trigger = self.ev.packet.trigger

        self.submodules.data_buf = buf = fifo.SyncFIFOBuffered(width=8, depth=128)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "DATA",
        #w       "reg_description": "Data received from the host will go into a FIFO.  This register reflects the contents of the top byte in that FIFO.",
        #w       "reg": [
        #w           { "name": "DATA",   "bits": 8, "attr": "RO", "description": "The top byte of the receive FIFO." }
        #w       ]
        #w   }
        #w }
        self.data = CSR(8)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "STATUS",
        #w       "reg_description": "Status about the contents of the OUT endpoint.",
        #w       "reg": [
        #w           { "name": "HAVE",  "bits": 1, "attr": "RO", "description": "`1` if there is data in the FIFO." },
        #w           { "name": "EPNO",  "bits": 4, "attr": "RO", "description": "The destination endpoint for the most recent SETUP packet." },
        #w           { "name": "IDLE",  "bits": 1, "attr": "RO", "description": "`1` if the packet has finished receiving." },
        #w           {                  "bits": 2 }
        #w       ]
        #w   }
        #w }
        self.status = CSRStatus(6)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "CTRL",
        #w       "reg_description": "Controls for managing `SETUP` packets.",
        #w       "reg": [
        #w           { "name": "ADVANCE", "bits": 1, "attr": "WO", "description": "Write a `1` here to advance the `DATA` FIFO." },
        #w           { "name": "ENABLE",  "bits": 1, "attr": "WO", "description": "Write a `1` here to enable recieving data" },
        #w           {                    "bits": 6 }
        #w       ]
        #w   }
        #w }
        self.ctrl = CSRStorage(2)

        # How to respond to requests:
        #  - 0 - ACK
        #  - 1 - NAK
        # Send a NAK if the buffer contains data, or if "ENABLE" has not been set.
        self.response = Signal()
        self.comb += self.response.eq(~(buf.readable | (self.ctrl.storage[1]>>1)))

        epno = Signal(4)
        is_idle = Signal()

        # Connect the buffer to the USB system
        self.data_recv_payload = Signal(8)
        self.data_recv_put = Signal()
        self.comb += [
            buf.din.eq(self.data_recv_payload),
            buf.we.eq(self.data_recv_put),
            self.data.w.eq(buf.dout),

            # When a "1" is written to ctrl, advance the FIFO
            buf.re.eq(self.ctrl.storage[0] & self.ctrl.re),

            self.status.status.eq(Cat(buf.readable, epno, is_idle)),
        ]

        # If we get a packet, turn off the "IDLE" flag and keep it off until the packet has finished.
        self.sync += [
            If(usb_core.commit & buf.readable,
                is_idle.eq(1),
            ).Elif(self.data_recv_put,
                is_idle.eq(0),
            )
        ]


class TriEndpointInterface(Module, AutoCSR):
    """

    Implements a CPU interface with three endpoints:
        * SETUP
        * EPIN
        * EPOUT

    Each endpoint has:
     * A FIFO with one end connected to CSRs and the other to the USB core.
     * Control bits.
     * A pending flag.

    An output FIFO is written to using CSR registers.
    An input FIFO is read using CSR registers.

    Extra CSR registers set the response type (ACK/NAK/STALL).
    """

    def __init__(self, iobuf, debug=False):

        # USB Core
        self.submodules.usb_core = usb_core = UsbTransfer(iobuf)

        self.submodules.pullup = GPIOOut(usb_core.iobuf.usb_pullup)
        self.iobuf = usb_core.iobuf

        self.eps_idx = eps_idx = Signal(5)
        self.comb += [
            self.eps_idx.eq(Cat(usb_core.tok == PID.IN, usb_core.endp == 0)),
        ]

        # Generate debug signals, in case debug is enabled.
        debug_packet_detected = Signal()
        debug_data_mux = Signal(8)
        debug_data_ready_mux = Signal()
        debug_sink_data = Signal(8)
        debug_sink_data_ready = Signal()
        debug_ack_response = Signal()

        # Delay the "put" signal (and corresponding data) by one cycle, to allow
        # the debug system to inhibit this write.  In practice, this doesn't
        # impact our latency at all as this signal runs at a rate of ~1 MHz.
        data_recv_put_delayed = Signal()
        data_recv_payload_delayed = Signal(8)
        self.sync += [
            data_recv_put_delayed.eq(usb_core.data_recv_put),
            data_recv_payload_delayed.eq(usb_core.data_recv_payload),
        ]

        ems = []
        trigger_all = []

        self.submodules.setup = setup_handler = ClockDomainsRenamer("usb_12")(SetupHandler(usb_core))
        ems.append(setup_handler.ev)
        trigger_all.append(setup_handler.trigger.eq(1)),

        self.submodules.epin = in_handler = ClockDomainsRenamer("usb_12")(InHandler(usb_core))
        ems.append(in_handler.ev)
        trigger_all.append(in_handler.trigger.eq(1)),

        self.submodules.epout = out_handler = ClockDomainsRenamer("usb_12")(OutHandler(usb_core))
        ems.append(out_handler.ev)
        trigger_all.append(out_handler.trigger.eq(1)),

        self.submodules.ev = ev.SharedIRQ(*ems)

        self.comb += [
            If(~iobuf.usb_pullup,
                *trigger_all,
            ),
        ]

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "ENABLE",
        #w       "reg_description": "Set a `1` to enable a given endpoint -- otherwise a STALL will be sent.",
        #w       "reg": [
        #w           { "name": "EPOUT",   "bits": 16, "attr": "WO", "description": "Set a `1` here to enable the given OUT endpoint" },
        #w           { "name": "EPIN",    "bits": 16, "attr": "WO", "description": "Set a `1` here to enable the given IN endpoint" }
        #w       ]
        #w   }
        #w }
        self.enable = CSRStorage(32)
        should_stall = Signal()

        self.comb += [
            should_stall.eq(~(self.enable.storage >> eps_idx)),
            If(debug_packet_detected,
                usb_core.data_send_payload.eq(debug_sink_data),
                usb_core.data_send_have.eq(debug_sink_data_ready),
                usb_core.sta.eq(~debug_sink_data_ready),
            ).Else(
                If(usb_core.tok == PID.SETUP,
                    # SETUP packet
                    setup_handler.data_recv_payload.eq(data_recv_payload_delayed),
                    setup_handler.data_recv_put.eq(data_recv_put_delayed),

                    # We aren't allowed to STALL a SETUP packet
                    usb_core.sta.eq(0),

                    # Always ACK a SETUP packet
                    usb_core.arm.eq(0),

                    setup_handler.trigger.eq(usb_core.commit),
                ).Elif(usb_core.tok == PID.IN,
                    # # IN packet (device-to-host)
                    usb_core.data_send_have.eq(in_handler.data_out),
                    usb_core.data_send_payload.eq(in_handler.data_out_have),

                    usb_core.sta.eq(should_stall),
                    usb_core.arm.eq(in_handler.response | should_stall),
                    in_handler.trigger.eq(usb_core.commit),
                ).Elif(usb_core.tok == PID.OUT,
                    # OUT packet (host-to-device)
                    out_handler.data_recv_payload.eq(data_recv_payload_delayed),
                    out_handler.data_recv_put.eq(data_recv_put_delayed),
                    usb_core.sta.eq(should_stall),
                    usb_core.arm.eq(out_handler.response | should_stall),
                    out_handler.trigger.eq(usb_core.commit),
                )
            )
        ]

        # # Add a signal to EP0OUT to drain it when we get a SETUP packet
        # # if it's not empty.
        # setup_do_drain = Signal()

        # # Endpoint controls
        # eps = []
        # trigger_all = []

        # # Add ep0out
        # self.submodules.ep0out = ep0out = ep = EndpointOut()
        # ep.drain_buffer.eq(~iobuf.usb_pullup | setup_do_drain)
        # ems.append(ep.ev)
        # trigger_all.append(ep.trigger.eq(1)),
        # eps.append(ep)

        # # Add ep0in
        # self.submodules.ep0in = ep0in = ep = EndpointIn()
        # ems.append(ep.ev)
        # trigger_all.append(ep.trigger.eq(1)),
        # eps.append(ep)

        # # Add the rest of the OUT endpoints
        # self.submodules.epnout = epnout = ep = EndpointOut()
        # ep.drain_buffer.eq(~iobuf.usb_pullup | setup_do_drain)
        # ems.append(ep.ev)
        # trigger_all.append(ep.trigger.eq(1)),
        # eps.append(ep)

        # # Add the rest of the IN endpoints
        # self.submodules.epnin = epnin = ep = EndpointIn()
        # ems.append(ep.ev)
        # trigger_all.append(ep.trigger.eq(1)),
        # eps.append(ep)


        # self.eps = eps = Array(eps)

        # # Setup packet causes ep0 in and ep0 out to reset
        # self.comb += [
        #     ep0out.reset.eq(usb_core.setup & ~debug_packet_detected),
        #     ep0in.reset.eq(usb_core.setup & ~debug_packet_detected),
        # ]

        # self.epnum = CSRStorage(5)

        # # If we get a SETUP packet, drain the EP0OUT FIFO.
        # # This works around a problem where there are two SETUP sequences
        # # back-to-back.  Without this, the two-byte CRC from the previous
        # # OUT packet will get added to the front of the subsequent DATA
        # # packet if the buffer isn't drained quickly enough.
        # # To work around this, assert `setup_do_drain` until the buffer
        # # is no longer readable.
        # last_start = Signal()
        # self.sync += [
        #     last_start.eq(usb_core.start),
        #     If(~debug_packet_detected,
        #         If(last_start,
        #             If(usb_core.tok == PID.SETUP,
        #                 If(~debug_packet_detected,
        #                     setup_do_drain.eq(1),
        #                 )
        #             )
        #         ).Elif(setup_do_drain & ~ep0out.obuf.readable,
        #             setup_do_drain.eq(0),
        #         )
        #     )
        # ]

        # # Wire up debug signals if required
        # if debug:
        #     debug_bridge = USBWishboneBridge(self.usb_core)
        #     self.submodules.debug_bridge = ClockDomainsRenamer("usb_12")(debug_bridge)
        #     self.comb += [
        #         debug_packet_detected.eq(~self.debug_bridge.n_debug_in_progress),
        #         debug_sink_data.eq(self.debug_bridge.sink_data),
        #         debug_sink_data_ready.eq(self.debug_bridge.sink_valid),
        #         debug_ack_response.eq(self.debug_bridge.send_ack | self.debug_bridge.sink_valid),
        #     ]

        # self.comb += [
        #     # This needs to be correct *before* token is finished, everything
        #     # else uses registered outputs.
        #     usb_core.sta.eq(((eps[eps_idx].response == EndpointResponse.STALL) & ~debug_packet_detected) & ~debug_sink_data_ready),
        #     usb_core.arm.eq(((eps[eps_idx].response == EndpointResponse.ACK) & ~debug_packet_detected) | debug_ack_response),
        #     usb_core.dtb.eq(eps[eps_idx].dtb.storage | debug_packet_detected),

        #     # Control signals
        #     If(~iobuf.usb_pullup,
        #         *trigger_all,
        #     ).Else(
        #         eps[eps_idx].trigger.eq(usb_core.commit & ~debug_packet_detected),
        #     ),

        #     If(debug_packet_detected,
        #         debug_data_mux.eq(debug_sink_data),
        #         debug_data_ready_mux.eq(debug_sink_data_ready),
        #     ).Else(
        #         debug_data_mux.eq(eps[eps_idx].ibuf.dout),
        #         debug_data_ready_mux.eq(eps[eps_idx].ibuf.readable),
        #     ),
        #     # FIFO
        #     # Host->Device[Out Endpoint] pathway
        #     eps[eps_idx].obuf.we.eq(data_recv_put_delayed & ~debug_packet_detected),
        #     eps[eps_idx].obuf.din.eq(data_recv_payload_delayed),
        #     # [In Endpoint]Device->Host pathway
        #     usb_core.data_send_have.eq(debug_data_ready_mux),
        #     usb_core.data_send_payload.eq(debug_data_mux),
        #     eps[eps_idx].ibuf.re.eq((usb_core.data_send_get & ~debug_packet_detected) | ~iobuf.usb_pullup),
        # ]

        error_count = Signal(8)
        self.sync += [
        #     If(usb_core.commit & ~debug_packet_detected,
        #         eps[eps_idx].last_tok.status.eq(usb_core.tok[2:]),
        #     ),

            # Reset the transfer state machine if it gets into an error
            If(usb_core.error,
                error_count.eq(error_count + 1),
                usb_core.reset.eq(1),
            ),
        ]
