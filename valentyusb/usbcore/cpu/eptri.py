#!/usr/bin/env python3

from enum import IntEnum

from migen import *
from migen.genlib import fifo
from migen.genlib import cdc

from litex.soc.integration.doc import AutoDoc, ModuleDoc
from litex.soc.interconnect import stream
from litex.soc.interconnect import wishbone
from litex.soc.interconnect import csr_eventmanager as ev
from litex.soc.interconnect.csr import CSRStorage, CSRStatus, CSRField, AutoCSR

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

class TriEndpointInterface(Module, AutoCSR, AutoDoc):
    """Implements a CPU interface with three FIFOs:
        * SETUP
        * IN
        * OUT

    Each of the three FIFOs has a relatively similar register set.

    Args
    ----

    iobuf (:obj:`io.IoBuf`): PHY interface to the raw pins.  This object
        encapsulate the pin interface to the outside world so that
        `TriEndpointInterface` does not need to have platform-specific
        IO handling.

    debug (bool, optional): Whether to add a debug bridge to this interface.
        Adding a debug bridge generates a Wishbone Master, which can take
        a large number of resources.  In exchange, it offers transparent debug.

    cdc (bool, optional): By default, ``eptri`` assumes that the CSR bus is in
        the same 12 MHz clock domain as the USB stack.  If ``cdc`` is set to
        True, then additional buffers will be placed on the ``.we`` and ``.re``
        lines to handle this difference.

    Attributes
    ----------

    debug_bridge (:obj:`wishbone.Interface`): The wishbone interface master for debug
        If `debug=True`, this attribute will contain the Wishbone Interface
        master for you to connect to your desired Wishbone bus.
    """

    def __init__(self, iobuf, debug=False, cdc=False):

        self.background = ModuleDoc(title="USB Device Tri-FIFO", body="""
            This is a three-FIFO USB device.  It presents one FIFO each for ``IN``, ``OUT``, and
            ``SETUP`` data.  This allows for up to 16 ``IN`` and 16 ``OUT`` endpoints
            without sacrificing many USB resources.

            USB supports four types of transfers: control, bulk, interrupt, and isochronous.
            This device does not yet support isochronous transfers, however it supports the
            other types of transfers.  It contains special logic for handing control transfers,
            including the handshake that is required.
            """)

        self.interrupt_bulk_transfers = ModuleDoc(title="Interrupt and Bulk Transfers", body="""
            Interrupt and bulk transfers are similar from an implementation standpoint --
            they differ only in terms of how often they are transmitted.

            These transfers can be made to any endpoint, and may even be interleaved.  However,
            due to the nature of `TriEndpointInterface` any attempt by the host to interleave
            transfers will result in a ``NAK``, and the host will retry later when the buffer
            is empty.

            IN Transfers
            ^^^^^^^^^^^^

            To make an ``IN`` transfer (i.e. to send data to the host), write the data to
            ``IN.DATA``.  This is a FIFO, and each write to this endpoint will advance the
            FIFO pointer automatically.  This FIFO is 64 bytes deep.  USB ``DATA`` packets
            contain a CRC16 checksum, which is automatically added to any ``IN`` transfers.

            `TriEndpointInterface` will continue to respond ``NAK`` until you arm the buffer.
            Do this by writing the endpoint number to ``IN.CTRL``.  This will tell the device
            that it should send the data the next time the host asks for it.

            Once the data has been transferred, the device will raise an interrupt and you
            can begin re-filling the buffer.

            To send an empty packet, avoid writing any data to ``IN.DATA`` and simply write
            the endpoint number to ``IN.CTRL``.

            OUT Transfers
            ^^^^^^^^^^^^^

            To respond to an ``OUT`` transfer (i.e. to receive data from the host), set the
            ``OUT.ENABLE`` bit.  This will tell the device to stop responding ``NAK`` and
            to accept any incoming data into a 66-byte FIFO.

            Once the host sends data, an interrupt will be raised and ``OUT.ENABLE`` will be
            set to ``0``.  This prevents any additional data from entering the FIFO while
            the device examines the data.

            The FIFO will contain two extra bytes, which are the two-byte CRC16 of the packet.
            You can safely discard these bytes.  Because of this, a zero-byte transfer will
            be two-bytes, and a full 64-byte transfer will be 66 bytes.

            To determine which endpoint the ``OUT`` packet was sent to, refer to
            ``OUT_STATUS.EPNO``, which is only updated when a successful packet is received.
            """)
        self.control_transfers = ModuleDoc(title="Control Transfers", body="""
            Control transfers are complicated, and are the first sort of transfer that
            the host uses.  Such transfers have three distinct phases.

            The first phase is the ``SETUP`` phase, where the host sends an 8-byte ``SETUP``
            packet.  These ``SETUP`` packets must always be acknowledged, so any such packet
            from the host will get loaded into a ``SETUP`` buffer immediately, and an interrupt
            event raised.  If, for some reason, the device hasn't drained this ``SETUP``
            buffer, the buffer will be cleared automatically.

            Following the ``SETUP`` phase is an ``IN`` or ``OUT`` packet.  This packet will
            be delayed by responding with ``NAK`` until the device acknowledges receipt
            of the ``SETUP`` packet.  To acknowledge receipt, set the ``SETUP_CTRL.HANDLED``
            bit.

            Once the ``SETUP`` packet is handled, the host will send an ``IN`` or ``OUT``
            packet.  If the host sends an ``OUT`` packet, then the ``OUT`` buffer must be
            cleared and the ``OUT_CTRL.ENABLE`` bit must be set.  The device will not
            accept any data as long as these two conditions are not met.

            If the host sends an ``IN`` packet, the device will respond with ``NAK`` if
            no data has queued.  To queue data, fill the ``IN.DATA`` buffer, then write
            ``0`` to ``IN.CTRL``.

            You can continue to fill the buffer (for ``IN`` packets) or drain the buffer
            and re-enable the endpoint (for ``OUT`` packets) until the host has finished
            the transfer.

            When the host has finished, it will send the opposite packet type.  If it
            is making ``IN`` transfers, it will send a single ``OUT`` packet, or if it
            is making ``OUT`` transfers it will send a single ``IN`` packet.
            `TriEndpointInterface` will automatically handle this final packet.

            Stalling an Endpoint
            ^^^^^^^^^^^^^^^^^^^^

            When the host sends a request that cannot be processed -- for example requesting
            a descriptor that does not exist -- the device must respond with ``STALL``.

            Each endpoint keeps track of its own ``STALL`` state, though a ``SETUP`` packet
            will clear the ``STALL`` state.

            To set or clear the ``STALL`` bit of an endpoint, write its endpoint number
            to ``IN_CTRL.EPNO`` with the ``IN_CTRL.STALL`` bit either set or clear.  If
            this bit is set, then the device will respond to the next ``IN`` packet from the
            host to that particular endpoint with ``STALL``.  If the bit is clear, then
            the next ``IN`` packet will be responded to with ``ACK`` and the contents of
            the ``IN`` FIFO.
            """)

        # USB Core
        self.submodules.usb_core = usb_core = UsbTransfer(iobuf)

        self.submodules.pullup = GPIOOut(usb_core.iobuf.usb_pullup)
        self.iobuf = usb_core.iobuf
       
        # Generate debug signals, in case debug is enabled.
        debug_packet_detected = Signal()

        # Wire up debug signals if required
        if debug:
            self.submodules.debug_bridge = debug_bridge = USBWishboneBridge(self.usb_core, cdc=cdc)
            self.comb += [
                debug_packet_detected.eq(~self.debug_bridge.n_debug_in_progress),
            ]

        ems = []

        # IRQ
        self.submodules.setup = setup_handler = ClockDomainsRenamer("usb_12")(SetupHandler(usb_core))
        self.comb += setup_handler.usb_reset.eq(usb_core.usb_reset)
        ems.append(setup_handler.ev)

        in_handler = ClockDomainsRenamer("usb_12")(InHandler(usb_core))
        self.submodules.__setattr__("in", in_handler)
        ems.append(in_handler.ev)

        self.submodules.out = out_handler = ClockDomainsRenamer("usb_12")(OutHandler(usb_core))
        ems.append(out_handler.ev)

        self.submodules.ev = ev.SharedIRQ(*ems)

        # If a debug packet comes in, the DTB should be 1.  Otherwise, the DTB should
        # be whatever the in_handler says it is.
        self.comb += usb_core.dtb.eq(in_handler.dtb | debug_packet_detected)
        usb_core_reset = Signal()

        # When the USB host sends a USB reset, set our address back to 0.
        self.address = ResetInserter()(CSRStorage(
            name="address",
            fields=[CSRField("addr", 7, description="Write the USB address from USB ``SET_ADDRESS`` packets.")],
            description="""Sets the USB device address, in order to ignore packets
                           going to other devices on the bus. This value is reset when the host
                           issues a USB Device Reset condition."""))
        self.comb += self.address.reset.eq(usb_core.usb_reset)

        self.submodules.stage = stage = ClockDomainsRenamer("usb_12")(ResetInserter()(FSM(reset_state="IDLE")))
        self.comb += stage.reset.eq(usb_core.usb_reset)
        stage_num = Signal(8)

        # invalid_states = Signal(8)
        # invalid_state_ce = Signal()

        # self.sync.usb_12 += [
        #     If(invalid_state_ce, invalid_states.eq(invalid_states+1)),
        # ]

        stage.act("IDLE",
            stage_num.eq(0),
            NextValue(usb_core.addr, self.address.storage),

            If(usb_core.start,
                NextState("CHECK_TOK")
            )
        )

        tok_waits = Signal(8)
        stage.act("CHECK_TOK",
            # stage_num.eq(1),
            NextValue(tok_waits, tok_waits + 1),
            If(usb_core.idle,
                NextState("IDLE"),
            ).Elif(usb_core.tok == PID.SETUP,
                NextState("SETUP"),
                setup_handler.begin.eq(1),
                # SETUP packets must be ACKed
                usb_core.sta.eq(0),
                usb_core.arm.eq(1),
            ).Elif(usb_core.tok == PID.IN,
                NextState("IN"),
                usb_core.sta.eq(in_handler.stalled),
                usb_core.arm.eq(in_handler.response),
            ).Elif(usb_core.tok == PID.OUT,
                NextState("OUT"),
                usb_core.sta.eq(out_handler.stalled),
                usb_core.arm.eq(out_handler.response),
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

            in_handler.reset.eq(1),

            # We aren't allowed to STALL a SETUP packet
            usb_core.sta.eq(0),

            # Always ACK a SETUP packet
            usb_core.arm.eq(1),

            # If the transfer size is nonzero, proceed to handle data packets

            If(debug_packet_detected,
                NextState("DEBUG")
            ),

            If(usb_core.setup,
                If(~setup_handler.have_data_stage,
                    NextState("WAIT_CONTROL_ACK"),
                ).Elif(setup_handler.is_in,
                    NextState("CONTROL_IN"),
                    usb_core.sta.eq(in_handler.stalled),
                    usb_core.arm.eq(in_handler.response),
                ).Else(
                    NextState("CONTROL_OUT"),
                    usb_core.sta.eq(out_handler.stalled),
                    usb_core.arm.eq(out_handler.response),
                )
            ).Elif(usb_core.end,
                # invalid_state_ce.eq(1),
                NextState("IDLE"),
            ),
        )

        stage.act("CONTROL_IN",
            stage_num.eq(4),
            # If(usb_core.tok == PID.SETUP, NextState("SETUP")),
            If(usb_core.endp == 0,
                If(usb_core.tok == PID.IN,
                    usb_core.data_send_have.eq(in_handler.data_out_have),
                    usb_core.data_send_payload.eq(in_handler.data_out),
                    in_handler.data_out_advance.eq(usb_core.data_send_get),

                    usb_core.sta.eq(in_handler.stalled),
                    usb_core.arm.eq(setup_handler.handled & in_handler.response),
                    If(in_handler.stalled & usb_core.poll,
                        in_handler.reset.eq(1),
                        NextState("IDLE")
                    ),
                    in_handler.trigger.eq(usb_core.commit),
                ).Elif(usb_core.tok == PID.OUT,
                    usb_core.sta.eq(0),
                    usb_core.arm.eq(1),
                    # After an IN transfer, the host sends an OUT
                    # packet.  We must ACK this and then return to IDLE.
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
                    usb_core.sta.eq(out_handler.stalled),
                    usb_core.arm.eq(setup_handler.handled & out_handler.response),
                    out_handler.trigger.eq(usb_core.commit),
                ).Elif(usb_core.tok == PID.IN,
                    usb_core.sta.eq(0),
                    usb_core.arm.eq(1),
                    NextState("WAIT_DONE"),
                )
            ),
        )


        # ACK the IN packet by sending a single OUT packet with no data
        stage.act("WAIT_CONTROL_ACK",
            stage_num.eq(6),
            usb_core.sta.eq(0),
            # Only continue once the buffer has been drained.
            usb_core.arm.eq(setup_handler.empty),
            If(usb_core.commit & setup_handler.empty,
                If(usb_core.tok == PID.IN,
                    in_handler.trigger.eq(1),
                ).Elif(usb_core.tok == PID.OUT,
                    out_handler.trigger.eq(1),
                ),
                NextState("IDLE")
            ),
        )

        stage.act("IN",
            stage_num.eq(8),
            If(usb_core.tok == PID.IN,
                # IN packet (device-to-host)
                usb_core.data_send_have.eq(in_handler.data_out_have),
                usb_core.data_send_payload.eq(in_handler.data_out),
                in_handler.data_out_advance.eq(usb_core.data_send_get),

                usb_core.sta.eq(in_handler.stalled),
                usb_core.arm.eq(in_handler.response),
                in_handler.trigger.eq(usb_core.commit),

                # After an IN transfer, the host sends an OUT
                # packet.  We must ACK this and then return to IDLE.
                If(usb_core.end,
                    NextState("IDLE"),
                ),
            ),
        )

        stage.act("OUT",
            stage_num.eq(9),
            # OUT packet (host-to-device)
            out_handler.data_recv_payload.eq(usb_core.data_recv_payload),
            out_handler.data_recv_put.eq(usb_core.data_recv_put),
            usb_core.sta.eq(out_handler.stalled),
            usb_core.arm.eq(out_handler.response),
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
            If(usb_core.commit,
                If(usb_core.tok == PID.IN,
                    in_handler.trigger.eq(1),
                ).Else(
                    out_handler.trigger.eq(1),
                ),
                NextState("IDLE"),
            ),
        )

        # error_count = Signal(8)
        self.comb += usb_core.reset.eq(usb_core.error | usb_core_reset)
        # self.sync.usb_12 += [
        #     # Reset the transfer state machine if it gets into an error
        #     If(usb_core.error,
        #         error_count.eq(error_count + 1),
        #     ),
        # ]

        # self.stage_num = CSRStatus(8)
        # self.last_stage_num = CSRStatus(8)
        # last_stage_num = Signal(8)
        # self.sync += If(stage_num != last_stage_num,
        #     self.last_stage_num.status.eq(last_stage_num),
        #     last_stage_num.eq(stage_num),
        # )
        # self.comb += self.stage_num.status.eq(stage_num)

        # self.error_count = CSRStatus(8)
        # # self.comb += self.error_count.status.eq(error_count)

        # self.tok_waits = CSRStatus(8)
        # self.comb += self.tok_waits.status.eq(tok_waits)

        # self.status = CSRStatus(8)
        # self.comb += self.status.status.eq(0)

        # self.invalid_states = CSRStatus(8)
        # self.comb += self.invalid_states.status.eq(invalid_states)

class SetupHandler(Module, AutoCSR):
    """Handle ``SETUP`` packets

    ``SETUP`` packets must always respond with ``ACK``.  They are followed by a ``DATA0``
    packet, and may be followed by additional DATA stages.

    Since SETUP packets must always be handled, there is a separate FIFO that
    handles this data.  Hence the name `eptri`.

    The device must always acknowledge the ``SETUP`` packet right away, but need
    not send the acknowledgement stage right away.  You can use this to parse
    the data at a leisurely pace.

    When the device receives a ``SETUP`` transaction, an interrupt will fire
    and the ``SETUP_STATUS`` register will have ``SETUP_STATUS.HAVE`` set to ``1``.
    Drain the FIFO by reading from ``SETUP_DATA``, then setting
    ``SETUP_CTRL.ADVANCE``.

    Attributes
    ----------

    reset : Signal
        Asserting this resets the entire SetupHandler object.  You should do this at boot, or if
        you're switching applications.

    begin : Signal
        Assert this when a ``SETUP`` token is received.  This will clear out the current buffer
        (if any) and prepare the endpoint to receive data.

    handled : Signal
        This gets set to ``1`` when the ``SETUP`` packet has been handled.

    is_in : Signal
        This is a ``1`` if the ``SETUP`` packet will be followed by an ``IN`` stage.

    usb_reset : Signal
        This signal feeds into the EventManager, which is used to indicate to the device
        that a USB reset has occurred.

    """

    def __init__(self, usb_core):

        self.reset = Signal()
        self.begin = Signal()
        self.usb_reset = Signal()

        # Register Interface
        self.data = data = CSRStatus(
            fields=[CSRField("data", 8, description="The next byte of ``SETUP`` data")],
            description="""Data from the last ``SETUP`` transactions.  It will be 10 bytes long, because
                           it will include the CRC16.  This is a FIFO, and the queue is advanced automatically."""
        )

        self.ctrl = ctrl = CSRStorage(
            fields=[
                CSRField("handled", offset=1, description="Write a ``1`` here to indicate SETUP has been handled.", pulse=True),
                CSRField("reset", description="Write a ``1`` here to reset the `SETUP` handler.", pulse=True),
            ],
            description="Controls for managing how to handle ``SETUP`` transactions."
        )

        self.status = status = CSRStatus(
            fields=[
                CSRField("have", description="``1`` if there is data in the FIFO."),
                CSRField("is_in", description="``1`` if an IN stage was detected."),
                CSRField("epno", 4, description="The destination endpoint for the most recent SETUP token."),
                CSRField("pend", description="``1`` if there is an IRQ pending."),
                CSRField("data", description="``1`` if a DATA stage is expected."),
            ],
            description="Status about the most recent ``SETUP`` transactions, and the state of the FIFO."
        )

        self.submodules.ev = ev.EventManager()
        self.ev.submodules.packet = ev.EventSourcePulse(name="ready",
                                            description="""
                                            Indicates a ``SETUP`` packet has arrived
                                            and is waiting in the ``SETUP`` FIFO.""")
        self.ev.submodules.reset = ev.EventSourcePulse(name="reset",
                                                        description="""
                                                        Indicates a USB ``RESET`` condition
                                                        has occurred, and the ``ADDRESS`` is now ``0``.""")
        self.ev.finalize()
        self.trigger = trigger = self.ev.packet.trigger
        self.pending = pending = self.ev.packet.pending
        self.comb += self.ev.reset.trigger.eq(self.usb_reset)

        self.data_recv_payload = data_recv_payload = Signal(8)
        self.data_recv_put = data_recv_put = Signal()

        # Since we must always ACK a SETUP packet, set this to 0.
        self.response = Signal()

        epno = Signal(4)

        class SetupHandlerInner(Module):
            def __init__(self):
                self.submodules.data = buf = fifo.SyncFIFOBuffered(width=8, depth=10)

                # Stays 0 until the packet is handled, at which point it becomes 1.
                self.handled = Signal()

                # Indicates which byte of `SETUP` data we're currently on.
                data_byte = Signal(4)

                # If the incoming `SETUP` token indicates there will be
                # a DATA stage, this will be set to 1.
                self.have_data_stage = have_data_stage = Signal()

                # If the incoming `SETUP` token is an OUT packet, this
                # will be 1.
                self.is_in = is_in = Signal()

                self.empty = Signal()
                self.comb += self.empty.eq(~buf.readable)

                # Wire up the `STATUS` register
                self.comb += [
                    status.fields.have.eq(buf.readable),
                    status.fields.is_in.eq(is_in),
                    status.fields.epno.eq(epno),
                    status.fields.pend.eq(pending),
                    status.fields.data.eq(have_data_stage),
                ]

                # Wire up the "SETUP" endpoint.
                self.comb += [
                    # Set the FIFO output to be the current buffer HEAD
                    data.fields.data.eq(buf.dout),

                    # Advance the FIFO when a byte is read
                    buf.re.eq(data.we),

                    If(usb_core.tok == PID.SETUP,
                        buf.din.eq(data_recv_payload),
                        buf.we.eq(data_recv_put),
                    ),

                    # Tie the trigger to the STATUS.HAVE bit
                    trigger.eq(buf.readable & usb_core.setup),
                ]

                self.sync += [
                    # When a `1` is written to the `CTRL.HANDLED` bit, indicate
                    # that the packet has been handled.
                    If(ctrl.fields.handled, self.handled.eq(1)),
                    # The 6th and 7th bytes of SETUP data are
                    # the wLength field.  If these are nonzero,
                    # then there will be a Data stage following
                    # this Setup stage.
                    If(data_recv_put,
                        If(data_byte == 0,
                            epno.eq(usb_core.endp),
                            is_in.eq(data_recv_payload[7]),
                        ).Elif(data_byte == 6,
                            If(data_recv_payload,
                                have_data_stage.eq(1),
                            ),
                        ).Elif(data_byte == 7,
                            If(data_recv_payload,
                                have_data_stage.eq(1),
                            ),
                        ),
                        data_byte.eq(data_byte + 1),
                    )
                ]

        self.submodules.inner = inner = ResetInserter()(SetupHandlerInner())
        self.comb += [
            inner.reset.eq(self.reset | self.begin | ctrl.fields.reset),
            self.ev.packet.clear.eq(self.begin),
        ]

        # Expose relevant Inner signals to the top
        self.handled = inner.handled
        self.have_data_stage = inner.have_data_stage
        self.is_in = inner.is_in
        self.empty = inner.empty


class InHandler(Module, AutoCSR):
    """Endpoint for Device->Host transactions.

    When a host requests data from a device, it sends an ``IN`` token.  The device
    should then respond with ``DATA0`, ``DATA1``, or ``NAK``.  This handler is
    responsible for managing this response, as well as supplying the USB system
    with data.

    To send data, fill the FIFO by writing bytes to ``IN_DATA``.  When you're ready
    to transmit, write the destination endpoint number to ``IN_CTRL``.

    Attributes
    ----------

    """
    def __init__(self, usb_core):
        self.dtb = Signal()

        # Keep track of the current DTB for each of the 16 endpoints
        dtbs = Signal(16, reset=1)

        # A list of endpoints that are stalled
        stall_status = Signal(16)

        self.submodules.data_buf = buf = ResetInserter()(fifo.SyncFIFOBuffered(width=8, depth=64))

        self.data = CSRStorage(
            fields=[
                CSRField("data", 8, description="The next byte to add to the queue."),
            ],
            description="""
                    Each byte written into this register gets added to an outgoing FIFO. Any
                    bytes that are written here will be transmitted in the order in which
                    they were added.  The FIFO queue is automatically advanced with each write.
                    The FIFO queue is 64 bytes deep.  If you exceed this amount, the result is undefined."""
        )
        self.status = CSRStatus(
            fields=[
                CSRField("have", description="This value is ``0`` if the FIFO is empty."),
                CSRField("idle", description="This value is ``1`` if the packet has finished transmitting."),
                CSRField("pend", offset=6, description="``1`` if there is an IRQ pending."),
            ],
            description="""Status about the IN handler.  As soon as you write to `IN_DATA`,
                           ``IN_STATUS.HAVE`` should go to ``1``."""
        )

        self.ctrl = ctrl = CSRStorage(
            fields=[
                CSRField("ep", 4, description="The endpoint number for the transaction that is queued in the FIFO."),
                CSRField("stall", description="Write a ``1`` here to stall the EP written in ``EP``.", pulse=True),
                CSRField("reset", description="Write a ``1`` here to clear the contents of the FIFO.", pulse=True),
            ],
            description="""
                        Enables transmission of data in response to ``IN`` tokens,
                        or resets the contents of the FIFO."""
        )

        self.submodules.ev = ev.EventManager()
        self.ev.submodules.packet = ev.EventSourcePulse(name="done", description="""
                            Indicates that the host has successfully transferred an `IN` packet,
                            and that the FIFO is now empty.
                            """)
        self.ev.finalize()
        self.trigger = self.ev.packet.trigger

        # Control bits
        ep_stall_mask = Signal(16)
        self.comb += [
            ep_stall_mask.eq(1 << ctrl.fields.ep),
        ]

        # Keep track of which endpoints are currently stalled
        self.stalled = Signal()
        self.comb += self.stalled.eq(stall_status >> usb_core.endp)
        self.sync += [
            If(ctrl.fields.reset,
                stall_status.eq(0),
            ).Elif(usb_core.setup | (ctrl.re & ~ctrl.fields.stall),
                # If a SETUP packet comes in, clear the STALL bit.
                stall_status.eq(stall_status & ~ep_stall_mask),
            ).Elif(ctrl.re,
                stall_status.eq(stall_status | ep_stall_mask),
            ),
        ]

        # How to respond to requests:
        #  - 0 - ACK
        #  - 1 - NAK
        self.response = Signal()

        self.reset = Signal()
        self.comb += [
            buf.reset.eq(ctrl.fields.reset | self.reset),
        ]

        # This value goes "1" when data is pending, and returns to "0" when it's done.
        queued = Signal()

        # This goes to "1" when "queued" is 1 when a "start" occurs.  It is used
        # to avoid skipping packets when a packet is queued during a transmission.
        transmitted = Signal()

        # Outgoing data will be placed on this signal
        self.data_out = Signal(8)

        # This is "1" if `data_out` contains data
        self.data_out_have = Signal()

        # Pulse this to advance the data output
        self.data_out_advance = Signal()

        # Pulse this to reset the DTB value
        # self.dtb_reset = Signal()

        # Used to detect when an IN packet finished
        is_in_packet = Signal()
        is_our_packet = Signal()

        self.comb += [
            # We will respond with "ACK" if the register matches the current endpoint number
            If(usb_core.endp == ctrl.fields.ep,
                self.response.eq(queued)
            ).Else(
                self.response.eq(0)
            ),

            # Wire up the "status" register
            self.status.fields.have.eq(buf.readable),
            self.status.fields.idle.eq(~queued),
            self.status.fields.pend.eq(self.ev.packet.pending),

            self.trigger.eq(is_in_packet & is_our_packet & usb_core.commit),

            self.dtb.eq(dtbs >> usb_core.endp),

            self.data_out.eq(buf.dout),
            self.data_out_have.eq(buf.readable),
            buf.re.eq(self.data_out_advance),
            buf.we.eq(self.data.re),
            buf.din.eq(self.data.storage),
            is_in_packet.eq(usb_core.tok == PID.IN),
            is_our_packet.eq(usb_core.endp == ctrl.fields.ep),
        ]

        self.sync += [
            If(self.reset,
                queued.eq(0),
                transmitted.eq(0),
                dtbs.eq(dtbs | (1 << ctrl.fields.ep)),
            )
            # When the user updates the `ctrl` register, enable writing.
            .Elif(ctrl.re & ~ctrl.fields.stall,
                queued.eq(1),
            )
            .Elif(usb_core.start & queued,
                transmitted.eq(1),
            )
            # When the USB core finishes operating on this packet,
            # de-assert the queue flag
            .Elif(usb_core.commit & transmitted,
                If(is_in_packet & is_our_packet & usb_core.arm & ~usb_core.sta,
                    queued.eq(0),
                    transmitted.eq(0),
                    # Toggle the "DTB" line if we transmitted data
                    dtbs.eq(dtbs ^ (1 << ctrl.fields.ep)),
                ),
            ),
        ]

class OutHandler(Module, AutoCSR):
    """
    Endpoint for Host->Device transaction

    When a host wants to send data to a device, it sends an ``OUT`` token.  The device
    should then respond with ``ACK``, or ``NAK``.  This handler is responsible for managing
    this response, as well as reading data from the USB subsystem.

    To enable receiving data, write a ``1`` to the ``OUT_CTRL.ENABLE`` bit.

    To drain the FIFO, read from ``OUT.DATA``.  Don't forget to re-
    enable the FIFO by ensuring ``OUT_CTRL.ENABLE`` is set after advancing the FIFO!

    Attributes
    ----------

    """
    def __init__(self, usb_core):

        self.submodules.data_buf = buf = ResetInserter()(fifo.SyncFIFOBuffered(width=8, depth=66))

        self.data = data = CSRStatus(
            fields=[
                CSRField("data", 8, description="The top byte of the receive FIFO."),
            ],
            description="""
                        Data received from the host will go into a FIFO.  This register
                        reflects the contents of the top byte in that FIFO.  Reading from
                        this register advances the FIFO pointer."""
        )

        self.status = CSRStatus(
            fields=[
                CSRField("have", description="``1`` if there is data in the FIFO."),
                CSRField("idle", reset=1, description="``1`` if the packet has finished receiving."),
                CSRField("epno", 4, description="The destination endpoint for the most recent ``OUT`` packet."),
                CSRField("pend", description="``1`` if there is an IRQ pending."),
                CSRField("arm", description="``1`` if the ``enable`` flag is set."),
            ],
            description="Status about the current state of the `OUT` endpoint."
        )

        self.ctrl = ctrl = CSRStorage(
            fields=[
                CSRField("enable", offset=1, description="Write a ``1`` here to enable receiving data"),
                CSRField("reset", pulse=True, description="Write a ``1`` here to reset the ``OUT`` handler"),
            ],
            description="Controls for receiving packet data."
        )

        self.stall = stall = CSRStorage(
            fields=[
                CSRField("epno", 4, description="The endpoint to update STALL status for"),
                CSRField("stall", description="``1`` to enable STALL, ``0`` to disable it"),
            ],
            description="Enables / disables STALL for a given endpoint"
        )

        self.submodules.ev = ev.EventManager()
        self.ev.submodules.packet = ev.EventSourcePulse(name="done",
                                                        description="""
                                                        Indicates that an ``OUT`` packet
                                                        has successfully been transferred
                                                        to the host.""")
        self.ev.finalize()
        self.trigger = self.ev.packet.trigger

        # If we start an OUT stage with data in the FIFO, ignore it
        ignore = Signal()
        self.sync += If(usb_core.poll, ignore.eq(buf.readable))

        arm = Signal()
        was_armed = Signal()
        epno = Signal(4)

        self.stalled = Signal()
        stall_status = Signal(16)
        ep_stall_mask = Signal(16)
        self.comb += [
            ep_stall_mask.eq(1 << stall.fields.epno),
        ]
        self.sync += [
            If(ctrl.fields.reset,
                stall_status.eq(0),
            ).Elif(usb_core.setup | (stall.re & ~stall.fields.stall),
                # If a SETUP packet comes in, clear the STALL bit.
                stall_status.eq(stall_status & ~ep_stall_mask),
            ).Elif(stall.re,
                stall_status.eq(stall_status | ep_stall_mask),
            ),
            If(ctrl.re & ctrl.fields.enable,
                arm.eq(1),
            ),
            self.stalled.eq(stall_status >> usb_core.endp),
        ]

        # How to respond to requests:
        #  - 1 - ACK
        #  - 0 - NAK
        # Send a NAK if the buffer contains data, or if "ENABLE" has not been set.
        self.response = Signal()
        is_idle = Signal(reset=1)
        self.comb += self.response.eq(arm & ~buf.readable & is_idle)

        # Used to detect when an OUT packet finished
        is_out_packet = Signal()
        is_our_packet = Signal()

        # Connect the buffer to the USB system
        self.data_recv_payload = Signal(8)
        self.data_recv_put = Signal()
        self.comb += [
            buf.din.eq(self.data_recv_payload),
            buf.we.eq(self.data_recv_put & ~ignore),
            buf.reset.eq(ctrl.fields.reset),
            self.data.fields.data.eq(buf.dout),

            # When data is read, advance the FIFO
            buf.re.eq(data.we),

            self.status.fields.have.eq(buf.readable),
            self.status.fields.idle.eq(is_idle),
            self.status.fields.epno.eq(epno),
            self.status.fields.pend.eq(self.ev.packet.pending),
            self.status.fields.arm.eq(arm),

            self.trigger.eq(~arm & was_armed),

            is_out_packet.eq(usb_core.tok == PID.OUT),
            is_our_packet.eq(usb_core.endp == epno),
        ]

        # If we get a packet, turn off the "IDLE" flag and keep it off until the packet has finished.
        self.sync += [
            was_armed.eq(arm),
            If(usb_core.commit & is_our_packet & is_out_packet & self.response & ~ignore,
                is_idle.eq(1),
                arm.eq(0),
            ).Elif(self.data_recv_put,
                is_idle.eq(0),
            ),
            If(self.trigger,
                epno.eq(usb_core.endp),
            ),
        ]