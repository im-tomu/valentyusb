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

"""
Register Interface:

pullup_out_read: Read the status of the USB "FS" pullup.
pullup_out_write: Write the USB "FS" pullup state

SETUP - Responding to a SETUP packet from the host
setup_read: Read the contents of the last SETUP transaction
setup_ack: Write a "1" here to advance the data_read fifo
setup_empty: "0" if there is no SETUP data.
setup_epno: The endpoint the SETUP packet was destined for

EPOUT - Data from the host to this device
epout_data_read: Read the contents of the last transaction on the EP0
epout_data_ack: Write a "1" here to advance the data_read fifo
epout_last_tok: Bits 2 and 3 of the last token, from the following table:
   USB_PID_OUT   = 0
   USB_PID_SOF   = 1
   USB_PID_IN    = 2
   USB_PID_SETUP = 3
epout_epno: Which endpoint contained the last data
epout_queued: A response is queued and has yet to be acknowledged by the host

EPIN - Requests from the host to read data from this device
epin_data_write: Write 8 bits to the EP0 queue
epin_data_empty: Return 1 if the queue is empty
epin_epno: Which endpoint the data is for.  You must write this byte to indicate data is ready to be sent.
epin_queued: A response is queued and has yet to be acknowledged by the host

ep_stall: a 32-bit field representing endpoitns to respond with STALL.
"""

class SetupHandler(Module, AutoCSR):
    """Handle SETUP packets.

    SETUP packets must always respond with ACK.  Sometimes, they are followed
    by DATA packets, but not always.

    Attributes
    ----------

    data : CSR
        Data from the last SETUP packet.  It will be 10 bytes long, because it will include the CRC16.  This is a FIFO; use `DATA_ACK` to advance the queue.

        .. wavedrom::
            :caption: data CSR Interface

            {
              "reg": [
                  { "name": "DATA",   "bits": 8, "attr": "RO", "description": "The next byte of SETUP data" }
              ]
            }

    status : CSRStatus
        Status about the most recent SETUP packet, and the state of the FIFO.

        .. wavedrom::
            :caption: status CSR Interface

            {
              "reg": [
                  { "name": "HAVE",  "bits": 1, "attr": "RO", "description": "`1` if there is data in the FIFO." },
                  {                  "bits": 1 },
                  { "name": "EPNO",  "bits": 4, "attr": "RO", "description": "The destination endpoint for the most recent SETUP packet." },
                  {                  "bits": 2 }
              ]
            }

    ctrl : CSRStorage
        Controls for managing `SETUP` packets.

        .. wavedrom::
            :caption: CTRL CSR Interface

            {
              "reg": [
                  { "name": "ADVANCE", "bits": 1, "attr": "WO", "description": "Write a `1` here to advance the `DATA` FIFO." },
                  {                    "bits": 7 }
              ]
            }

    """


    def __init__(self, usb_core):
        self.submodules.ev = ev.EventManager()
        self.ev.submodules.packet = ev.EventSourcePulse()
        self.ev.finalize()
        self.trigger = self.ev.packet.trigger

        self.data_recv_payload = Signal(8)
        self.data_recv_put = Signal()
        self.have_data_stage = Signal()

        self.submodules.data = buf = ClockDomainsRenamer({"write": "usb_12", "read": "sys"})(ResetInserter()(fifo.SyncFIFOBuffered(width=8, depth=10)))
        self.data = CSR(8)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "STATUS",
        #w       "reg_description": "Status about the most recent SETUP packet, and the state of the FIFO.",
        #w       "reg": [
        #w           { "name": "HAVE",  "bits": 1, "attr": "RO", "description": "`1` if there is data in the FIFO." },
        #w           {                  "bits": 1 },
        #w           { "name": "EPNO",  "bits": 4, "attr": "RO", "description": "The destination endpoint for the most recent SETUP packet." },
        #w           { "name": "PEND",  "bits": 1, "attr": "RO", "description": "`1` if there is an IRQ pending." },
        #w           {                  "bits": 1 }
        #w       ]
        #w   }
        #w }
        self.status = CSRStatus(7)

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

        self.empty = Signal()
        self.comb += self.empty.eq(~buf.readable)

        epno = Signal(4)

        # Wire up the `STATUS` register
        self.comb += self.status.status.eq(Cat(buf.readable, Signal(), epno, self.ev.packet.pending))

        # Wire up the "SETUP" endpoint.
        self.comb += [
            # Set the FIFO output to be the current buffer HEAD
            self.data.w.eq(buf.dout),

            # Advance the FIFO when anything is written to the control bit
            buf.re.eq(self.ctrl.re & self.ctrl.storage[0]),

            If(usb_core.tok == PID.SETUP,
                buf.din.eq(self.data_recv_payload),
                buf.we.eq(self.data_recv_put),
            ),

            # Tie the trigger to the STATUS.HAVE bit
            self.trigger.eq(buf.readable & usb_core.end),
        ]

        # When we get the start of a SETUP packet, update the `epno` value.
        check_reset = Signal()
        data_bytes = Signal(3)
        self.sync.usb_12 += [
            check_reset.eq(usb_core.start),
            If(check_reset,
                If(usb_core.tok == PID.SETUP,
                    epno.eq(usb_core.endp),
                    buf.reset.eq(1),
                    data_bytes.eq(0),
                    self.have_data_stage.eq(0),
                ).Else(
                    buf.reset.eq(0),
                )
            ).Else(
                buf.reset.eq(0),
            ),

            # The 6th and 7th bytes of SETUP data are
            # the wLength field.  If these are nonzero,
            # then there will be a Data stage following
            # this Setup stage.
            If(self.data_recv_put,
                data_bytes.eq(data_bytes + 1),
                If(self.data_recv_payload,
                    If(data_bytes == 6,
                        self.have_data_stage.eq(1),
                    ).Elif(data_bytes == 7,
                        self.have_data_stage.eq(1),
                    )
                )
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
        self.ev.submodules.packet = ev.EventSourcePulse()
        self.ev.finalize()
        self.trigger = self.ev.packet.trigger
        self.dtb = Signal()

        # Keep track of the current DTB for each of the 16 endpoints
        dtbs = Signal(16, reset=0xffff)

        self.submodules.data_buf = buf = fifo.SyncFIFOBuffered(width=8, depth=128)

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
        #w           { "name": "HAVE",    "bits": 1, "attr": "RO", "description": "This value is '0' if the FIFO is empty." },
        #w           { "name": "IDLE",    "bits": 1, "attr": "RO", "description": "This value is '1' if the packet has finished transmitting." },
        #w           {                    "bits": 4 },
        #w           { "name": "PEND",    "bits": 1, "attr": "RO", "description": "`1` if there is an IRQ pending." },
        #w           {                    "bits": 1 }
        #w       ]
        #w   }
        #w }
        self.status = CSRStatus(7)

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
        self.specials.crc_readable = cdc.MultiReg(~buf.readable, xxxx_readable)

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

        # Pulse this to reset the DTB value
        self.dtb_reset = Signal()

        # Used to detect when an IN packet finished
        is_in_packet = Signal()
        is_our_packet = Signal()

        self.comb += [
            # We will respond with "ACK" if the register matches the current endpoint number
            If(usb_core.endp == self.epno.storage,
                self.response.eq(queued)
            ).Else(
                self.response.eq(0)
            ),

            # Wire up the "status" register
            self.status.status.eq(
                Cat(~xxxx_readable, ~queued, Signal(4), self.ev.packet.pending)
            ),
            self.trigger.eq(is_in_packet & is_our_packet & usb_core.commit),

            self.dtb.eq(dtbs >> usb_core.endp),

            self.data_out.eq(buf.dout),
            self.data_out_have.eq(buf.readable),
            buf.re.eq(self.data_out_advance),
            buf.we.eq(self.data.re),
            buf.din.eq(self.data.storage),
            is_in_packet.eq(usb_core.tok == PID.IN),
            is_our_packet.eq(usb_core.endp == self.epno.storage),
        ]

        self.sync += [
            # Toggle the "DTB" line if we transmitted data
            If(self.dtb_reset,
                dtbs.eq(dtbs | (1 << self.epno.storage)),
            )
            # When the user updates the `epno` register, enable writing.
            .Elif(self.epno.re,
                queued.eq(1)
            )
            # When the USB core finishes operating on this packet,
            # de-assert the queue flag
            .Elif(usb_core.commit,
                If(is_in_packet & is_our_packet,
                    queued.eq(0),
                ),
                If(usb_core.arm & ~usb_core.sta,
                    dtbs.eq(dtbs ^ (1 << self.epno.storage)),
                ),
            ),
        ]

class OutHandler(Module, AutoCSR):
    def __init__(self, usb_core):
# EPOUT - Data from the host to this device
        self.submodules.ev = ev.EventManager()
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
        #w           { "name": "IDLE",  "bits": 1, "attr": "RO", "description": "`1` if the packet has finished receiving." },
        #w           { "name": "EPNO",  "bits": 4, "attr": "RO", "description": "The destination endpoint for the most recent SETUP packet." },
        #w           { "name": "PEND",  "bits": 1, "attr": "RO", "description": "`1` if there is an IRQ pending." },
        #w           {                  "bits": 1 }
        #w       ]
        #w   }
        #w }
        self.status = CSRStatus(7)

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
        #  - 1 - ACK
        #  - 0 - NAK
        # Send a NAK if the buffer contains data, or if "ENABLE" has not been set.
        self.response = Signal()
        self.ctrl_response = Signal()
        have_data = Signal()
        self.comb += self.response.eq(have_data & self.ctrl.storage[1])

        epno = Signal(4)
        is_idle = Signal(reset=1)

        # Used to detect when an OUT packet finished
        is_out_packet = Signal()
        is_our_packet = Signal()

        # Connect the buffer to the USB system
        self.data_recv_payload = Signal(8)
        self.data_recv_put = Signal()
        self.comb += [
            buf.din.eq(self.data_recv_payload),
            buf.we.eq(self.data_recv_put),
            self.data.w.eq(buf.dout),

            # When a "1" is written to ctrl, advance the FIFO
            buf.re.eq(self.ctrl.storage[0] & self.ctrl.re),

            self.status.status.eq(Cat(buf.readable, is_idle, epno, self.ev.packet.pending)),
            self.trigger.eq((usb_core.commit & is_our_packet & is_out_packet & self.response) | self.ctrl_response),

            is_out_packet.eq(usb_core.tok == PID.OUT),
            is_our_packet.eq(usb_core.endp == epno),
        ]

        # If we get a packet, turn off the "IDLE" flag and keep it off until the packet has finished.
        self.sync += [
            If(usb_core.commit & buf.readable,
                is_idle.eq(1),
            ).Elif(self.data_recv_put,
                is_idle.eq(0),
            ),
            If(is_out_packet,
                epno.eq(usb_core.endp),
            ),
            If(self.ctrl.re,
                have_data.eq(self.ctrl.storage[1]),
            ),
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
            self.eps_idx.eq(Cat(usb_core.endp, usb_core.tok == PID.IN)),
        ]
        
        # Generate debug signals, in case debug is enabled.
        debug_packet_detected = Signal()
        debug_data_mux = Signal(8)
        debug_data_ready_mux = Signal()
        debug_sink_data = Signal(8)
        debug_sink_data_ready = Signal()
        debug_ack_response = Signal()

        # Wire up debug signals if required
        if debug:
            debug_bridge = USBWishboneBridge(self.usb_core)
            self.submodules.debug_bridge = ClockDomainsRenamer("usb_12")(debug_bridge)
            self.comb += [
                debug_packet_detected.eq(~self.debug_bridge.n_debug_in_progress),
            ]

        ems = []
        trigger_all = []

        # IRQ
        self.submodules.setup = setup_handler = ClockDomainsRenamer("usb_12")(SetupHandler(usb_core))
        ems.append(setup_handler.ev)
        trigger_all.append(setup_handler.trigger.eq(1)),

        in_handler = ClockDomainsRenamer("usb_12")(InHandler(usb_core))
        self.submodules.__setattr__("in", in_handler)
        ems.append(in_handler.ev)
        trigger_all.append(in_handler.trigger.eq(1)),

        self.submodules.out = out_handler = ClockDomainsRenamer("usb_12")(OutHandler(usb_core))
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
        #w       "reg_name": "ENABLE_OUT0",
        #w       "reg_description": "Set a `1` to enable endpoints 0-7 OUT -- otherwise a STALL will be sent.",
        #w       "reg": [
        #w           { "name": "EPOUT",   "bits": 8, "attr": "WO", "description": "Set a `1` here to enable the given OUT endpoint" },
        #w       ]
        #w   }
        #w }
        self.enable_out0 = CSRStorage(8)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "ENABLE_OUT1",
        #w       "reg_description": "Set a `1` to enable endpoints 8-15 IN -- otherwise a STALL will be sent.",
        #w       "reg": [
        #w           { "name": "EPOUT",   "bits": 8, "attr": "WO", "description": "Set a `1` here to enable the given OUT endpoint" },
        #w       ]
        #w   }
        #w }
        self.enable_out1 = CSRStorage(8)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "ENABLE_IN0",
        #w       "reg_description": "Set a `1` to enable endpoints 0-7 IN -- otherwise a STALL will be sent.",
        #w       "reg": [
        #w           { "name": "EPIN",    "bits": 8, "attr": "WO", "description": "Set a `1` here to enable the given IN endpoint" }
        #w       ]
        #w   }
        #w }
        self.enable_in0 = CSRStorage(8)

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "ENABLE_IN1",
        #w       "reg_description": "Set a `1` to enable endpoints 8-15 IN -- otherwise a STALL will be sent.",
        #w       "reg": [
        #w           { "name": "EPIN",    "bits": 8, "attr": "WO", "description": "Set a `1` here to enable the given IN endpoint" }
        #w       ]
        #w   }
        #w }
        self.enable_in1 = CSRStorage(8)

        enable = Signal(32)
        should_stall = Signal()
        usb_core_reset = Signal()

        #w {
        #w   "reg_definition": {
        #w       "reg_name": "ADDRESS",
        #w       "reg_description": "Sets the USB device address, to ignore packets going to other devices.",
        #w       "reg": [
        #w           { "name": "ADDRESS", "bits": 7, "attr": "WO", "description": "Write the USB address from USB `SET_ADDRESS packets.`" },
        #w           {                    "bits": 1 }
        #w       ]
        #w   }
        #w }
        self.address = ResetInserter()(CSRStorage(7, name="address"))
        self.comb += self.address.reset.eq(usb_core.usb_reset)
        # self.sync.usb_12 += If(self.address.re, usb_address.eq(self.address.storage))

        self.submodules.stage = stage = ClockDomainsRenamer("usb_12")(ResetInserter()(FSM(reset_state="IDLE")))
        self.comb += stage.reset.eq(usb_core.usb_reset)
        stage_num = Signal(8)

        # If the SETUP stage should have data, this will be 1
        setup_data_stage = Signal()

        # Which of the 10 SETUP bytes (8 + CRC16) we're currently looking at
        setup_data_byte = Signal(4)
        setup_data_byte_ce = Signal()
        setup_data_byte_rst = Signal()

        # 1 if it's an IN packet, 0 if it's an OUT
        setup_is_in = Signal()

        invalid_states = Signal(8)
        invalid_state_ce = Signal()

        self.sync.usb_12 += [
            If(invalid_state_ce, invalid_states.eq(invalid_states+1)),
            If(setup_data_byte_rst,
                setup_data_byte.eq(0),
                setup_is_in.eq(0),
                setup_data_stage.eq(0),
            ).Elif(setup_data_byte_ce,
                setup_data_byte.eq(setup_data_byte + 1),
                If(setup_data_byte == 0,
                    setup_is_in.eq(usb_core.data_recv_payload[7]),
                ).Elif(setup_data_byte == 6,
                    If(usb_core.data_recv_payload,
                        setup_data_stage.eq(1)
                    )
                ).Elif(setup_data_byte == 7,
                    If(usb_core.data_recv_payload,
                        setup_data_stage.eq(1)
                    )
                ),
            ),
        ]

        stage.act("IDLE",
            stage_num.eq(0),
            NextValue(usb_core.addr, self.address.storage),
            setup_data_byte_rst.eq(1),

            If(usb_core.start,
                NextState("CHECK_TOK")
            )
        )

        tok_waits = Signal(8)
        stage.act("CHECK_TOK",
            stage_num.eq(1),
            NextValue(tok_waits, tok_waits + 1),
            If(usb_core.idle,
                NextState("IDLE"),
            ).Elif(usb_core.tok == PID.SETUP,
                NextState("SETUP"),
                # SETUP packets must be ACKed
                usb_core.sta.eq(0),
                usb_core.arm.eq(1),
            ).Elif(usb_core.tok == PID.IN,
                NextState("IN"),
                usb_core.sta.eq(should_stall),
                usb_core.arm.eq(in_handler.response | should_stall),
            ).Elif(usb_core.tok == PID.OUT,
                NextState("OUT"),
                usb_core.sta.eq(should_stall),
                usb_core.arm.eq(out_handler.response | should_stall),
            ).Else(
                NextState("IDLE"),
            )
        )

        if debug:
            stage.act("DEBUG",
                stage_num.eq(2),
                usb_core.data_send_payload.eq(self.debug_bridge.sink_data),
                usb_core.data_send_have.eq(self.debug_bridge.sink_valid),
                usb_core.sta.eq(0),
                If(usb_core.endp == 0,
                    usb_core.arm.eq(self.debug_bridge.send_ack | self.debug_bridge.sink_valid),
                ).Else(
                    usb_core.arm.eq(0)
                ),
                usb_core.dtb.eq(1),
                If(~debug_packet_detected,
                    NextState("IDLE")
                )
            )
        else:
            stage.act("DEBUG", NextState("IDLE"))

        stage.act("SETUP",
            stage_num.eq(3),
            # SETUP packet
            setup_handler.data_recv_payload.eq(usb_core.data_recv_payload),
            setup_handler.data_recv_put.eq(usb_core.data_recv_put),

            in_handler.dtb_reset.eq(1),

            # We aren't allowed to STALL a SETUP packet
            usb_core.sta.eq(0),

            # Always ACK a SETUP packet
            usb_core.arm.eq(1),

            setup_handler.trigger.eq(usb_core.commit),

            setup_data_byte_ce.eq(usb_core.data_recv_put),

            # If the transfer size is nonzero, proceed to handle data packets

            If(debug_packet_detected,
                NextState("DEBUG")
            ),

            If(usb_core.setup,
                If(setup_is_in,
                    If(setup_data_stage,
                        NextState("CONTROL_IN"),
                        usb_core.sta.eq(~out_handler.response & should_stall),
                        usb_core.arm.eq(in_handler.response | should_stall),
                    ).Else(
                        NextState("WAIT_CONTROL_ACK_IN"),
                    )
                ).Else(
                    If(setup_data_stage,
                        NextState("CONTROL_OUT"),
                        usb_core.sta.eq(~out_handler.response & should_stall),
                        usb_core.arm.eq(out_handler.response | should_stall),
                    ).Else(
                        NextState("WAIT_CONTROL_ACK_OUT"),
                    )
                )
            ).Elif(usb_core.end,
                invalid_state_ce.eq(1),
                NextState("IDLE"),
            ),
        )

        stage.act("CONTROL_IN",
            stage_num.eq(4),
            If(usb_core.endp == 0,
                If(usb_core.tok == PID.IN,
                    usb_core.data_send_have.eq(in_handler.data_out_have),
                    usb_core.data_send_payload.eq(in_handler.data_out),
                    in_handler.data_out_advance.eq(usb_core.data_send_get),

                    usb_core.sta.eq(should_stall),
                    usb_core.arm.eq(should_stall | (setup_handler.empty & in_handler.response)),
                    usb_core.dtb.eq(in_handler.dtb),
                    in_handler.trigger.eq(usb_core.commit),

                ).Elif(usb_core.tok == PID.OUT,
                    usb_core.sta.eq(0),
                    usb_core.arm.eq(1),
                    # After an IN transfer, the host sends an OUT
                    # packet.  We must ACK this and then return to IDLE.
                    out_handler.ctrl_response.eq(1),
                    NextState("WAIT_DONE"),
                )
            )
        )

        stage.act("CONTROL_OUT",
            stage_num.eq(5),
            If(usb_core.endp == 0,
                If(usb_core.tok == PID.OUT,
                    out_handler.data_recv_payload.eq(usb_core.data_recv_payload),
                    out_handler.data_recv_put.eq(usb_core.data_recv_put),
                    usb_core.sta.eq(should_stall),
                    usb_core.arm.eq(should_stall | (setup_handler.empty & out_handler.response)),
                    out_handler.trigger.eq(usb_core.commit),
                ).Elif(usb_core.tok == PID.IN,
                    usb_core.sta.eq(0),
                    usb_core.arm.eq(setup_handler.empty),
                    NextState("WAIT_DONE"),
                )
            ),
        )

        # ACK the IN packet by sending a single OUT packet with no data
        stage.act("WAIT_CONTROL_ACK_IN",
            stage_num.eq(6),
            usb_core.sta.eq(0),
            # Only continue once the buffer has been drained.
            usb_core.arm.eq(setup_handler.empty),
            usb_core.dtb.eq(1),
            If(usb_core.end & setup_handler.empty,
                NextState("IDLE")
            ),
        )

        # ACK the OUT packet by sending a single IN packet with no data
        stage.act("WAIT_CONTROL_ACK_OUT",
            stage_num.eq(7),
            usb_core.sta.eq(0),
            # Only continue once the buffer has been drained.
            usb_core.arm.eq(setup_handler.empty),
            usb_core.dtb.eq(1),
            If(usb_core.data_end & setup_handler.empty,
                # usb_core_reset.eq(1),
                NextState("IDLE")
            ),
        )

        stage.act("IN",
            stage_num.eq(8),
            # If(usb_core.tok == PID.IN,
                # # IN packet (device-to-host)
                usb_core.data_send_have.eq(in_handler.data_out_have),
                usb_core.data_send_payload.eq(in_handler.data_out),
                in_handler.data_out_advance.eq(usb_core.data_send_get),

                usb_core.sta.eq(should_stall),
                usb_core.arm.eq(in_handler.response | should_stall),
                usb_core.dtb.eq(in_handler.dtb),
                in_handler.trigger.eq(usb_core.commit),

                # After an IN transfer, the host sends an OUT
                # packet.  We must ACK this and then return to IDLE.
                If(usb_core.end,
                    NextState("IDLE"),
                ),
            # ),
        )

        stage.act("OUT",
            stage_num.eq(9),
            # OUT packet (host-to-device)
            out_handler.data_recv_payload.eq(usb_core.data_recv_payload),
            out_handler.data_recv_put.eq(usb_core.data_recv_put),
            usb_core.sta.eq(should_stall),
            usb_core.arm.eq(out_handler.response | should_stall),
            out_handler.trigger.eq(usb_core.commit),

            # After an OUT transfer, the host sends an IN
            # packet.  We must ACK this and then return to IDLE.
            If(usb_core.end,
                NextState("IDLE"),
            ),
        )

        stage.act("WAIT_DONE",
            stage_num.eq(10),
            usb_core.sta.eq(0),
            usb_core.arm.eq(1),
            If(usb_core.end,
                NextState("IDLE"),
            ),
        )

        self.comb += [
            enable.eq(Cat(self.enable_out0.storage, self.enable_out1.storage, self.enable_in0.storage, self.enable_in1.storage)),
            should_stall.eq(~(enable >> eps_idx)),
        ]

        error_count = Signal(8)
        self.comb += usb_core.reset.eq(usb_core.error | usb_core_reset)
        self.sync.usb_12 += [
            # Reset the transfer state machine if it gets into an error
            If(usb_core.error,
                error_count.eq(error_count + 1),
            ),
        ]

        self.stage_num = CSRStatus(8)
        self.comb += self.stage_num.status.eq(stage_num)

        self.error_count = CSRStatus(8)
        self.comb += self.error_count.status.eq(error_count)

        self.tok_waits = CSRStatus(8)
        self.comb += self.tok_waits.status.eq(tok_waits)

        self.status = CSRStatus(8)
        # reset_count = Signal(4)
        # self.sync += If(usb_core.usb_reset, reset_count.eq(reset_count + 1))
        self.comb += self.status.status.eq(Cat(setup_data_byte,
                                               setup_data_byte_ce,
                                               setup_data_byte_rst))

        self.invalid_states = CSRStatus(8)
        self.comb += self.invalid_states.status.eq(invalid_states)