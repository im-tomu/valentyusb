#!/usr/bin/env python3

from enum import IntEnum

from migen import *
from migen.genlib import fifo
from migen.genlib.cdc import *

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
from .usbwishboneburstbridge import USBWishboneBurstBridge

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

    relax_timing (bool, optional): ``eptri`` is optimized for small devices that
        do not require high speed routing. As such, combinatorial logic is preferred.
        Set ``relax_timing=True`` to enable registered accesses for certain operations
        to allow for a higher Fmax at the expense of logic cells.

    Attributes
    ----------

    debug_bridge (:obj:`wishbone.Interface`): The wishbone interface master for debug
        If `debug=True`, this attribute will contain the Wishbone Interface
        master for you to connect to your desired Wishbone bus.
    """

    def __init__(self, iobuf, debug=False, burst=False, cdc=False, relax_timing=False):

        self.background = ModuleDoc(title="USB Device Tri-FIFO", body="""
            This is a three-FIFO USB device.  It presents one FIFO each for ``IN``, ``OUT``, and
            ``SETUP`` data.  This allows for up to 16 ``IN`` and 16 ``OUT`` endpoints
            without sacrificing many FPGA resources.

            USB supports four types of transfers: control, bulk, interrupt, and isochronous.
            This device does not yet support isochronous transfers, however it supports the
            other types of transfers.
            """)

        self.interrupt_bulk_transfers = ModuleDoc(title="Interrupt and Bulk Transfers", body="""
            Interrupt and bulk transfers are similar from an implementation standpoint --
            they differ only in terms of how often they are transmitted.

            These transfers can be made to any endpoint, and may even be interleaved.  However,
            due to the nature of ``TriEndpointInterface`` any attempt by the host to interleave
            transfers will result in a ``NAK``, and the host will retry later when the buffer
            is empty.

            IN Transfers
            ^^^^^^^^^^^^

            To make an ``IN`` transfer (i.e. to send data to the host), write the data to
            ``IN_DATA``.  This is a FIFO, and each write to this endpoint will advance the
            FIFO pointer automatically.  This FIFO is 64 bytes deep.  USB ``DATA`` packets
            contain a CRC16 checksum, which is automatically added to any ``IN`` transfers.

            ``TriEndpointInterface`` will continue to respond ``NAK`` until you arm the buffer.
            Do this by writing the endpoint number to ``IN_CTRL.EPNO``.  This will tell the device
            that it should send the data the next time the host asks for it.

            Once the data has been transferred, the device will raise an interrupt and you
            can begin re-filling the buffer, or fill it with data for a different endpoint.

            To send an empty packet, avoid writing any data to ``IN_DATA`` and simply write
            the endpoint number to ``IN_CTRL.EPNO``.

            The CRC16 will be automatically appended to the end of the transfer.

            OUT Transfers
            ^^^^^^^^^^^^^

            To respond to an ``OUT`` transfer (i.e. to receive data from the host), enable
            a particular endpoint by writing to ``OUT_CTRL.EPNO`` with the ``OUT_CTRL.ENABLE``
            bit set.  This will tell the device to stop responding ``NAK`` to that particular
            endpoint and to accept any incoming data into a 66-byte FIFO, provided the FIFO
            is empty.

            Once the host sends data, an interrupt will be raised and that particular endpoint's
            ``ENABLE`` will be set to ``0``.  This prevents any additional data from entering
            the FIFO while the device examines the data.

            The FIFO will contain two extra bytes, which are the two-byte CRC16 of the packet.
            You can safely discard these bytes.  Because of this, a zero-byte transfer will
            be two-bytes, and a full 64-byte transfer will be 66 bytes.

            To determine which endpoint the ``OUT`` packet was sent to, refer to
            ``OUT_STATUS.EPNO``.  This field is only updated when a successful packet is received,
            and will not change until the ``OUT`` FIFO is re-armed.

            The ``OUT`` FIFO will continue to respond to the host with with ``NAK`` until the
            ``OUT_EV_PENDING.DONE`` bit is cleared.

            Additionally, to continue receiving data on that particular endpoint, you will need
            to re-enable it by writing the endpoint number, along with the ``OUT_CTRL.ENABLE``
            to ``OUT_CTRL``.
            """)
        self.control_transfers = ModuleDoc(title="Control Transfers", body="""
            Control transfers are complicated, and are the first sort of transfer that
            the host uses.  Such transfers have three distinct phases.

            The first phase is the ``SETUP`` phase, where the host sends an 8-byte ``SETUP``
            packet.  These ``SETUP`` packets must always be acknowledged, so any such packet
            from the host will get loaded into the ``SETUP`` FIFO immediately, and an interrupt
            event raised.  If, for some reason, the device hasn't drained this ``SETUP``
            FIFO from a previous transaction, the FIFO will be cleared automatically.

            Once the ``SETUP`` packet is handled, the host will send an ``IN`` or ``OUT``
            packet.  If the host sends an ``OUT`` packet, then the ``OUT`` buffer must be
            cleared, the ``OUT.DONE`` interrupt handled, and the ``OUT_CTRL.ENABLE`` bit
            must be set for the appropriate endpoint, usually EP0.  The device will not
            accept any data as long as these three conditions are not met.

            If the host sends an ``IN`` packet, the device will respond with ``NAK`` if
            no data has queued.  To queue data, fill the ``IN_DATA`` buffer, then write
            ``0`` to ``IN_CTRL``.

            You can continue to fill the buffer (for ``IN`` packets) or drain the buffer
            and re-enable the endpoint (for ``OUT`` packets) until the host has finished
            the transfer.

            When the host has finished, it will send the opposite packet type.  If it
            is making ``IN`` transfers, it will send a single ``OUT`` packet, or if it
            is making ``OUT`` transfers it will send a single ``IN`` packet.
            You must handle this transaction yourself.

            Stalling an Endpoint
            ^^^^^^^^^^^^^^^^^^^^

            When the host sends a request that cannot be processed -- for example requesting
            a descriptor that does not exist -- the device must respond with ``STALL``.

            Each endpoint keeps track of its own ``STALL`` state, though a ``SETUP`` packet
            will clear the ``STALL`` state for the specified endpoint (usually EP0).

            To set or clear the ``STALL`` bit of an ``IN`` endpoint, write its endpoint number
            to ``IN_CTRL.EPNO`` with the ``IN_CTRL.STALL`` bit either set or clear.  If
            this bit is set, then the device will respond to the next ``IN`` packet from the
            host to that particular endpoint with ``STALL``.  If the bit is clear, then
            the next ``IN`` packet will be responded to with ``ACK`` and the contents of
            the ``IN`` FIFO.

            To stall an ``OUT`` endpoint, write to ``OUT_CTRL.EPNO`` with the ``OUT_CTRL.STALL``
            and ``OUT_CTRL.ENABLE`` bits set.  To unstall, write to ``OUT_CTRL.EPNO`` with the
            ``OUT_CTRL.STALL`` bit and ``OUT_CTRL.ENABLE`` bits cleared.  Note that
            ``OUT_CTRL.ENABLE`` indicates whether any response sould be sent at all, which is
            why it must be set or cleared at the same time.
            """)

        # USB Core
        self.submodules.usb_core = usb_core = UsbTransfer(iobuf, cdc=cdc)

        self.submodules.pullup = GPIOOut(usb_core.iobuf.usb_pullup)
        self.iobuf = usb_core.iobuf

        # Generate debug signals, in case debug is enabled.
        debug_packet_detected = Signal()
        debug_phase = Signal()

        # Wire up debug signals if required
        if debug and not burst:
            self.submodules.debug_bridge = debug_bridge = USBWishboneBridge(self.usb_core,
                                                                            cdc=cdc,
                                                                            relax_timing=relax_timing)
            self.comb += [
                debug_packet_detected.eq(~self.debug_bridge.n_debug_in_progress),
                debug_phase.eq(self.debug_bridge.data_phase),
            ]
        elif debug and burst:
            self.submodules.debug_bridge = debug_bridge = USBWishboneBurstBridge(self.usb_core)
            self.comb += [
                debug_packet_detected.eq(~self.debug_bridge.n_debug_in_progress),
                debug_phase.eq(self.debug_bridge.data_phase),
            ]

        ems = []

        # When the USB host sends a USB reset, set our address back to 0.
        self.address = ResetInserter()(CSRStorage(
            name="address",
            fields=[CSRField("addr", 7, description="Write the USB address from USB ``SET_ADDRESS`` packets.")],
            description="""
                Sets the USB device address, in order to ignore packets
                going to other devices on the bus. This value is reset when the host
                issues a USB Device Reset condition.
            """))
        self.comb += self.address.reset.eq(usb_core.usb_reset)

        self.next_ev = CSRStatus(
            fields=[
                CSRField("in", 1, description="``1`` if the next event is an ``IN`` event"),
                CSRField("out", 1, description="``1`` if the next event is an ``OUT`` event"),
                CSRField("setup", 1, description="``1`` if the next event is an ``SETUP`` event"),
                CSRField("reset", 1, description="``1`` if the next event is a ``RESET`` event"),
            ],
            description="""
                In ``eptri``, there are three endpoints.  It is possible for an IRQ to fire
                and have all three bits set.  Under these circumstances it can be difficult
                to know which event to process first.  Use this register to determine which
                event needs to be processed first.
                Only one bit will ever be set at a time.
            """,
        )

        # Handlers
        self.submodules.setup = setup_handler = SetupHandler(usb_core, cdc=cdc)
        self.comb += setup_handler.usb_reset.eq(usb_core.usb_reset)
        ems.append(setup_handler.ev)

        in_handler = InHandler(usb_core, cdc=cdc)
        self.submodules.__setattr__("in", in_handler)
        ems.append(in_handler.ev)

        self.submodules.out = out_handler = OutHandler(usb_core, cdc=cdc)
        ems.append(out_handler.ev)

        self.submodules.ev = ev.SharedIRQ(*ems)

        in_next = Signal()
        out_next = Signal()
        self.sync += [
            If(usb_core.usb_reset,
                in_next.eq(0),
                out_next.eq(0),
            # If the in_handler is set but not the out_handler, that one is next
            ).Elif(in_handler.ev.packet.pending & ~out_handler.ev.packet.pending,
                in_next.eq(1),
                out_next.eq(0),
            # If the out_handler is set first, mark that as `next`
            ).Elif(~in_handler.ev.packet.pending & out_handler.ev.packet.pending,
                in_next.eq(0),
                out_next.eq(1),
            # If neither is set, then clear the bits.
            ).Elif(~in_handler.ev.packet.pending & ~out_handler.ev.packet.pending,
                in_next.eq(0),
                out_next.eq(0),
            ),
            # If both are set, don't do anything.
        ]
        self.comb += [
            If(setup_handler.ev.reset.pending,
                self.next_ev.fields.reset.eq(1),
            ).Elif(in_next,
                getattr(self.next_ev.fields, "in").eq(1),
            ).Elif(out_next,
                self.next_ev.fields.out.eq(out_next),
            ).Elif(setup_handler.ev.packet.pending,
                self.next_ev.fields.setup.eq(1),
            )
        ]

        # If a debug packet comes in, the DTB should be 1.  Otherwise, the DTB should
        # be whatever the in_handler says it is.
        if cdc:
            self.comb += [
                If(debug_packet_detected,
                   usb_core.dtb.eq( 1 ^ debug_phase ),
                ).Else(
                    usb_core.dtb.eq(in_handler.dtb_12),
                )
            ]
        else:
            self.comb += [
                If(debug_packet_detected,
                   usb_core.dtb.eq( 1 ^ debug_phase ),
                ).Else(
                    usb_core.dtb.eq(in_handler.dtb),
                )
            ]
        usb_core_reset = Signal()

        self.submodules.stage = stage = ResetInserter()(ClockDomainsRenamer("usb_12")(FSM(reset_state="IDLE")))
        self.comb += stage.reset.eq(usb_core.usb_reset_12)

        if cdc:
            self.submodules.address_12 = BusSynchronizer(7, "sys", "usb_12")
            self.comb += self.address_12.i.eq(self.address.storage)
            stage.act("IDLE",
                NextValue(usb_core.addr, self.address_12.o),

                If(usb_core.start,
                    NextState("CHECK_TOK")
                )
            )
        else:
            stage.act("IDLE",
                NextValue(usb_core.addr, self.address.storage),

                If(usb_core.start,
                    NextState("CHECK_TOK")
                )
            )

        stage.act("CHECK_TOK",
            If(usb_core.idle,
                NextState("IDLE"),
            ).Elif(usb_core.tok == PID.SETUP,
                NextState("SETUP"),
                setup_handler.begin.eq(1),
                in_handler.dtb_reset.eq(1),
                # SETUP packets must be ACKed unconditionally
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
            # SETUP packet
            setup_handler.data_recv_payload.eq(usb_core.data_recv_payload),
            setup_handler.data_recv_put.eq(usb_core.data_recv_put),

            # We aren't allowed to STALL a SETUP packet
            usb_core.sta.eq(0),

            # Always ACK a SETUP packet
            usb_core.arm.eq(1),

            If(debug_packet_detected,
                NextState("DEBUG")
            ),

            If(usb_core.end,
                NextState("IDLE"),
            ),
        )

        stage.act("IN",
            If(usb_core.tok == PID.IN,
                # IN packet (device-to-host)
                usb_core.data_send_have.eq(in_handler.data_out_have),
                usb_core.data_send_payload.eq(in_handler.data_out),
                in_handler.data_out_advance.eq(usb_core.data_send_get),

                usb_core.sta.eq(in_handler.stalled),
                usb_core.arm.eq(in_handler.response),

                # After an IN transfer, the host sends an OUT
                # packet.  We must ACK this and then return to IDLE.
                If(usb_core.end,
                    NextState("IDLE"),
                ),
            ),
        )

        stage.act("OUT",
            If(usb_core.tok == PID.OUT,
                # OUT packet (host-to-device)
                out_handler.data_recv_payload.eq(usb_core.data_recv_payload),
                out_handler.data_recv_put.eq(usb_core.data_recv_put),

                usb_core.sta.eq(out_handler.stalled),
                usb_core.arm.eq(out_handler.response),

                # After an OUT transfer, the host sends an IN
                # packet.  We must ACK this and then return to IDLE.
                If(usb_core.end,
                    NextState("IDLE"),
                ),
            ),
        )

        self.comb += usb_core.reset.eq(usb_core.error | usb_core_reset)

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

    epno : Signal(4)
        The endpoint number the SETUP packet came in on (probably is always ``0``)

    is_in : Signal
        This is a ``1`` if the ``SETUP`` packet will be followed by an ``IN`` stage.

    usb_reset : Signal
        This signal feeds into the EventManager, which is used to indicate to the device
        that a USB reset has occurred.

    """

    def __init__(self, usb_core, cdc=False):

        self.reset = Signal()
        self.begin = Signal()
        self.begin_sys = Signal()
        self.specials += MultiReg(self.begin, self.begin_sys)
        self.epno = epno = Signal()
        self.usb_reset = Signal()

        # Register Interface
        self.data = data = CSRStatus(
            fields=[CSRField("data", 8, description="The next byte of ``SETUP`` data")],
            description="""Data from the last ``SETUP`` transactions.  It will be 10 bytes long, because
                           it will include the CRC16.  This is a FIFO, and the queue is advanced automatically."""
        )

        self.ctrl = ctrl = CSRStorage(
            fields=[
                CSRField("reset", offset=5, description="Write a ``1`` here to reset the `SETUP` handler.", pulse=True),
            ],
            description="Controls for managing how to handle ``SETUP`` transactions."
        )

        self.status = status = CSRStatus(
            fields=[
                CSRField("epno", 4, description="The destination endpoint for the most recent SETUP token."),
                CSRField("have", description="``1`` if there is data in the FIFO."),
                CSRField("pend", description="``1`` if there is an IRQ pending."),
                CSRField("is_in", description="``1`` if an IN stage was detected."),
                CSRField("data", description="``1`` if a DATA stage is expected."),
            ],
            description="Status about the most recent ``SETUP`` transactions, and the state of the FIFO."
        )

        self.submodules.ev = ev.EventManager()
        self.ev.submodules.packet = ev.EventSourcePulse(name="ready",
                                            description="""
                                            Indicates a ``SETUP`` packet has arrived
                                            and is waiting in the ``SETUP`` FIFO.""")
        self.ev.submodules.reset = ev.EventSourceProcess(name="reset",
                                                        description="""
                                                        Indicates a USB ``RESET`` condition
                                                        has occurred, and the ``ADDRESS`` is now ``0``.""")
        self.ev.finalize()
        self.trigger = trigger = self.ev.packet.trigger
        self.pending = pending = self.ev.packet.pending
        self.comb += self.ev.reset.trigger.eq(~self.usb_reset)

        self.data_recv_payload = data_recv_payload = Signal(8)
        self.data_recv_put = data_recv_put = Signal()

        # Since we must always ACK a SETUP packet, set this to 0.
        self.response = Signal()

        class SetupHandlerInner(Module):
            def __init__(self, cdc=False):
                if cdc:
                    self.submodules.setupfifo = ResetInserter(["usb_12", "sys"])(ClockDomainsRenamer({"write": "usb_12", "read": "sys"})(
                        fifo.AsyncFIFO(width=8, depth=16)))  # 10
                else:
                    self.submodules.setupfifo = fifo.SyncFIFOBuffered(width=8, depth=10)

                # Indicates which byte of `SETUP` data we're currently on.
                data_byte = Signal(4)

                # If the incoming `SETUP` token indicates there will be
                # a DATA stage, this will be set to 1.
                self.have_data_stage = have_data_stage = Signal()

                # If the incoming `SETUP` token is an OUT packet, this
                # will be 1.
                self.is_in = is_in = Signal()

                self.empty = Signal()
                self.comb += self.empty.eq(~self.setupfifo.readable)

                # Wire up the `STATUS` register
                if cdc:
                    epno_sys = Signal(epno.nbits)

                    self.specials += MultiReg(is_in, status.fields.is_in)
                    self.specials += MultiReg(self.setupfifo.readable, status.fields.have)
                    self.specials += MultiReg(pending, status.fields.pend)
                    self.submodules.epnosync = BusSynchronizer(epno_sys.nbits, "usb_12", "sys")
                    self.comb += [
                        self.epnosync.i.eq(epno),
                        epno_sys.eq(self.epnosync.o),
                    ]
                    self.specials += MultiReg(have_data_stage, status.fields.data)
                    self.comb += [
                        status.fields.epno.eq(epno_sys),
                    ]
                else:
                    self.comb += [
                        status.fields.have.eq(self.setupfifo.readable),
                        status.fields.is_in.eq(is_in),
                        status.fields.epno.eq(epno),
                        status.fields.pend.eq(pending),
                        status.fields.data.eq(have_data_stage),
                    ]

                # Wire up the "SETUP" endpoint.
                setup_sys = Signal()
                if cdc:
                    self.specials += MultiReg(usb_core.setup, setup_sys)
                else:
                    self.comb += setup_sys.eq(usb_core.setup)

                self.comb += [
                    # Set the FIFO output to be the current buffer HEAD
                    data.fields.data.eq(self.setupfifo.dout),

                    # Advance the FIFO when a byte is read
                    self.setupfifo.re.eq(data.we & self.setupfifo.readable),

                    If(usb_core.tok == PID.SETUP,
                        self.setupfifo.din.eq(data_recv_payload),
                        self.setupfifo.we.eq(data_recv_put),
                    ),

                    # Tie the trigger to the STATUS.HAVE bit
                    trigger.eq(self.setupfifo.readable & setup_sys),
                ]

                self.sync.usb_12 += [
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

        if cdc:
            # this is a cute little dance we have to do.
            # it is necessary for reset pulse ordering to be strictly usb_12 (write side) -> sys (read side)
            # if you reset sys first, then usb_12 later, you can get a glitch on the "readable" signal
            # while the other side of the FIFO is pending a reset. This causes an error in the USB test bench
            # Thus, we compute a "reset_signal" in the sys domain, capture it to usb_12 using BlindTransfer,
            # then we bring it *back* into sys using a MultiReg to enforce strict reset ordering
            reset_signal = Signal()
            self.submodules.inner = inner = ResetInserter(["sys", "usb_12"])(SetupHandlerInner(cdc=cdc))
            self.submodules.setupreset = BlindTransfer("sys", "usb_12")
            self.comb += [
                self.setupreset.i.eq(reset_signal),
                inner.reset_usb_12.eq(self.setupreset.o),
            ]
            self.specials += MultiReg(inner.reset_usb_12, inner.reset_sys)
            self.comb += [
                reset_signal.eq(self.reset | self.begin_sys | ctrl.fields.reset),
                self.ev.packet.clear.eq(self.begin_sys),
            ]
        else:
            self.submodules.inner = inner = ResetInserter()(ClockDomainsRenamer({"usb_12":"sys"})(SetupHandlerInner()))
            self.comb += [
                inner.reset.eq(self.reset | self.begin | ctrl.fields.reset),
                self.ev.packet.clear.eq(self.begin),
            ]

        # Expose relevant Inner signals to the top
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
    def __init__(self, usb_core, cdc=False):
        if cdc:
            self.dtb_12 = Signal()

            # Keep track of the current DTB for each of the 16 endpoints
            dtbs = Signal(16, reset=0x0001)
            dtbs_12 = Signal(16, reset=0x0001)
            self.submodules.dtbsync = BusSynchronizer(16, "usb_12", "sys")
            self.comb += [
                self.dtbsync.i.eq(dtbs_12),
                dtbs.eq(self.dtbsync.o)
            ]
        else:
            self.dtb = Signal()

            # Keep track of the current DTB for each of the 16 endpoints
            dtbs = Signal(16, reset=0x0001)

        # A list of endpoints that are stalled
        stall_status = Signal(16)

        if cdc:
            self.submodules.data_buf = buf = ResetInserter(["usb_12", "sys"])(ClockDomainsRenamer({"write":"sys","read":"usb_12"})(fifo.AsyncFIFOBuffered(width=8, depth=64)))
        else:
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

        self.ctrl = ctrl = CSRStorage(
            fields=[
                CSRField("epno", 4, description="The endpoint number for the transaction that is queued in the FIFO."),
                CSRField("reset", offset=5, description="Write a ``1`` here to clear the contents of the FIFO.", pulse=True),
                CSRField("stall", description="Write a ``1`` here to stall the EP written in ``EP``."),
            ],
            description="""
                Enables transmission of data in response to ``IN`` tokens,
                or resets the contents of the FIFO."""
        )

        self.status = CSRStatus(
            fields=[
                CSRField("idle", description="This value is ``1`` if the packet has finished transmitting."),
                CSRField("have", offset=4, description="This value is ``0`` if the FIFO is empty."),
                CSRField("pend", offset=5, description="``1`` if there is an IRQ pending."),
            ],
            description="""
                Status about the IN handler.  As soon as you write to `IN_DATA`,
                ``IN_STATUS.HAVE`` should go to ``1``."""
        )

        self.submodules.ev = ev.EventManager()
        self.ev.submodules.packet = ev.EventSourcePulse(name="done", description="""
            Indicates that the host has successfully transferred an ``IN`` packet,
            and that the FIFO is now empty.
            """)
        self.ev.finalize()

        # Control bits
        ep_stall_mask = Signal(16)
        self.comb += [
            ep_stall_mask.eq(1 << ctrl.fields.epno),
        ]

        # Keep track of which endpoints are currently stalled
        if cdc:
            self.stalled = Signal()
            stalled_sys = Signal()
            setup_sys = Signal()
            self.specials += MultiReg(usb_core.setup, setup_sys)
            endp_sys = Signal(4)
            self.submodules.endpsync = BusSynchronizer(4, "usb_12", "sys")
            self.comb += [
                self.endpsync.i.eq(usb_core.endp),
                endp_sys.eq(self.endpsync.o),
            ]
            self.comb += stalled_sys.eq(stall_status >> endp_sys)
            stall_status_12 = Signal(16)
            self.submodules.stall_status_sync = BusSynchronizer(16, "sys", "usb_12")
            self.comb += [
                self.stall_status_sync.i.eq(stall_status),
                stall_status_12.eq(self.stall_status_sync.o),
            ]
            self.comb += self.stalled.eq(stall_status_12 >> usb_core.endp)
            self.sync += [
                If(ctrl.fields.reset,
                    stall_status.eq(0),
                ).Elif(setup_sys | (ctrl.re & ~ctrl.fields.stall),
                    # If a SETUP packet comes in, clear the STALL bit.
                    stall_status.eq(stall_status & ~ep_stall_mask),
                ).Elif(ctrl.re,
                    stall_status.eq(stall_status | ep_stall_mask),
                ),
            ]
        else:
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

        # This value goes "1" when data is pending, and returns to "0" when it's done.
        queued = Signal()
        was_queued = Signal()

        # This goes to "1" when "queued" is 1 when a "start" occurs.  It is used
        # to avoid skipping packets when a packet is queued during a transmission.
        transmitted = Signal()

        self.dtb_reset = Signal()
        if cdc:
            response_sys = Signal()
            transmitted_12 = Signal()
            self.specials += MultiReg(transmitted_12, transmitted)
            commit_sys = Signal()
            self.specials += MultiReg(usb_core.commit, commit_sys)

            self.sync += [
                buf.reset_sys.eq(ctrl.fields.reset | (commit_sys & transmitted & queued)),
            ]
            self.submodules.bufressync = BlindTransfer("sys", "usb_12")
            self.comb += [
                self.bufressync.i.eq(buf.reset_sys),
                buf.reset_usb_12.eq(self.bufressync.o),
            ]
        else:
            self.comb += [
                buf.reset.eq(ctrl.fields.reset | (usb_core.commit & transmitted & queued)),
            ]

        # Outgoing data will be placed on this signal
        self.data_out = Signal(8)

        # This is "1" if `data_out` contains data
        self.data_out_have = Signal()

        # Pulse this to advance the data output
        self.data_out_advance = Signal()

        # Used to detect when an IN packet finished
        is_our_packet = Signal()
        is_in_packet = Signal()

        if cdc:
            is_in_packet_sys = Signal()
            is_our_packet_sys = Signal()
            epno12 = Signal(4)
            self.submodules.epno12sync = BusSynchronizer(4, "sys", "usb_12")
            self.comb += [
                self.epno12sync.i.eq(ctrl.fields.epno),
                epno12.eq(self.epno12sync.o),
            ]

            #self.specials += MultiReg(queued & is_our_packet & is_in_packet, self.response, "usb_12") # We will respond with "ACK" if the register matches the current endpoint number
            queued12 = Signal()
            self.specials += MultiReg(queued12, queued)
            self.sync += was_queued.eq(queued)
            self.comb += self.response.eq(queued12 & is_our_packet & is_in_packet)  ## this needs to be fast
            self.comb += response_sys.eq(queued & is_our_packet_sys & is_in_packet_sys)

            self.comb += self.dtb_12.eq(dtbs_12 >> usb_core.endp)
            self.dtb_sys = Signal()
            self.comb += self.dtb_sys.eq(dtbs >> endp_sys)

            readable_sys = Signal()
            self.specials += MultiReg(buf.readable, readable_sys)
            self.comb += [
                # Wire up the "status" register
                self.status.fields.have.eq(readable_sys),
                self.status.fields.idle.eq(~queued),
                self.status.fields.pend.eq(self.ev.packet.pending),

                # Cause a trigger event when the `queued` value goes to 0
                self.ev.packet.trigger.eq(~queued & was_queued),

                self.data_out.eq(buf.dout),
                buf.re.eq(self.data_out_advance & is_in_packet_sys & is_our_packet),
                is_our_packet.eq(usb_core.endp == epno12),
                is_our_packet_sys.eq(endp_sys == ctrl.fields.epno),
                is_in_packet.eq(usb_core.tok == PID.IN),
                self.data_out_have.eq(buf.readable),
            ]
            self.specials += MultiReg(usb_core.tok == PID.IN, is_in_packet_sys)

            self.comb += [
                buf.we.eq(self.data.re),
                buf.din.eq(self.data.storage),
            ]

            #### HAZARD: ctrl_re_12 and reset_12 are coming from separate pulse synchronizers
            #### The routine below assumes the synchronization is perfect, but if they are offset,
            #### Then the functionality is broken. The problem is that reset is a "pulse" type field,
            #### but then the control signal is also considered by the ".re" accessor. In a fully
            #### synchronous system, Reset would arrive on the same edge as the ctrl.re accessor,
            #### and the If/then logic below would work.
            #### However, if due to differential routing delays, one synchronizer tends to cross
            #### a domain before the other, it's possible that reset "beats" ctrl.re by one cycle
            #### after crossing the synchronizer, thus causing the state machine to first go into
            #### reset, and then subsequently execute the "then" clause considering the "ctrl.re & ~stall"
            #### logic. A work-around to this is to pulse-stretch the reset signal by one extra clk12
            #### so that if ctrl.re happens to be a cycle late going through its synchronizer, everything
            #### works. This /should/ be ok, but of course, when crossing asynchronous boundaries it is
            #### theoretically possible to take longer.
            #### Really, this state machine should be redesigned for asynchronous comms, but I don't
            #### understand it well enough -- I am just trying to fix synchronization bugs.
            self.submodules.ctrl_re_12 = BlindTransfer("sys", "usb_12")
            ctrl_re_12 = Signal()
            self.comb += [
                self.ctrl_re_12.i.eq(ctrl.re),
                ctrl_re_12.eq(self.ctrl_re_12.o),
            ]
            reset_12 = Signal()
            reset_12_short = Signal()
            reset_12_delay = Signal()
            self.submodules.reset_12 = BlindTransfer("sys", "usb_12")
            self.comb += [
                self.reset_12.i.eq(ctrl.fields.reset),
                reset_12_short.eq(self.reset_12.o),
                reset_12.eq(reset_12_short | reset_12_delay),
            ]
            self.sync.usb_12 += [
                reset_12_delay.eq(reset_12_short),
            ]

            ctrl_stall_12 = Signal()
            self.sync.usb_12 += [
                ctrl_stall_12.eq(ctrl.fields.stall), # not a multireg because this has to clear before the .re signal for it to catch
                If(reset_12,
                    queued12.eq(0),
                    transmitted_12.eq(0),
                    dtbs_12.eq(0x0001),
                ).Elif(self.dtb_reset,
                    dtbs_12.eq(dtbs_12 | 1),
                )
                # When the user updates the `ctrl` register, enable writing.
                .Elif(ctrl_re_12 & ~ctrl_stall_12,
                    queued12.eq(1),
                )
                .Elif(usb_core.poll & self.response,
                    transmitted_12.eq(1),
                )
                # When the USB core finishes operating on this packet,
                # de-assert the queue flag
                .Elif(usb_core.commit & transmitted_12 & self.response & ~self.stalled,
                    queued12.eq(0),
                    transmitted_12.eq(0),
                    # Toggle the "DTB" line if we transmitted data
                    dtbs_12.eq(dtbs_12 ^ (1 << epno12)),
                )
            ]
        else:
            self.comb += [
                # We will respond with "ACK" if the register matches the current endpoint number
                self.response.eq(queued & is_our_packet & is_in_packet),

                # Wire up the "status" register
                self.status.fields.have.eq(buf.readable),
                self.status.fields.idle.eq(~queued),
                self.status.fields.pend.eq(self.ev.packet.pending),

                # Cause a trigger event when the `queued` value goes to 0
                self.ev.packet.trigger.eq(~queued & was_queued),

                self.dtb.eq(dtbs >> usb_core.endp),

                self.data_out.eq(buf.dout),
                self.data_out_have.eq(buf.readable),
                buf.re.eq(self.data_out_advance & is_in_packet & is_our_packet),
                buf.we.eq(self.data.re),
                buf.din.eq(self.data.storage),
                is_our_packet.eq(usb_core.endp == ctrl.fields.epno),
                is_in_packet.eq(usb_core.tok == PID.IN),
            ]

            self.sync += [
                If(ctrl.fields.reset,
                    queued.eq(0),
                    was_queued.eq(0),
                    transmitted.eq(0),
                    dtbs.eq(0x0001),
                ).Elif(self.dtb_reset,
                    dtbs.eq(dtbs | 1),
                )
                    # When the user updates the `ctrl` register, enable writing.
                    .Elif(ctrl.re & ~ctrl.fields.stall,
                    queued.eq(1),
                          )
                    .Elif(usb_core.poll & self.response,
                    transmitted.eq(1),
                          )
                    # When the USB core finishes operating on this packet,
                    # de-assert the queue flag
                    .Elif(usb_core.commit & transmitted & self.response & ~self.stalled,
                    queued.eq(0),
                    transmitted.eq(0),
                    # Toggle the "DTB" line if we transmitted data
                    dtbs.eq(dtbs ^ (1 << ctrl.fields.epno)),
                          ).Else(
                    was_queued.eq(queued),
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
    def __init__(self, usb_core, cdc=False):
        if cdc:
            self.submodules.data_buf = buf = ResetInserter(["sys", "usb_12"])(ClockDomainsRenamer({"write":"usb_12","read":"sys"})(fifo.AsyncFIFO(width=8, depth=128))) # 66
        else:
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

        self.ctrl = ctrl = CSRStorage(
            fields=[
                CSRField("epno", 4, description="The endpoint number to update the ``enable`` and ``status`` bits for."),
                CSRField("enable", description="Write a ``1`` here to enable receiving data"),
                CSRField("reset", pulse=True, description="Write a ``1`` here to reset the ``OUT`` handler"),
                CSRField("stall", description="Write a ``1`` here to stall an endpoint"),
            ],
            description="""
                Controls for receiving packet data.  To enable an endpoint, write its value to ``epno``,
                with the ``enable`` bit set to ``1`` to enable an endpoint, or ``0`` to disable it.
                Resetting the OutHandler will set all ``enable`` bits to 0.

                Similarly, you can adjust the ``STALL`` state by setting or clearing the ``stall`` bit."""
        )

        self.status = CSRStatus(
            fields=[
                CSRField("epno", 4, description="The destination endpoint for the most recent ``OUT`` packet."),
                CSRField("have", description="``1`` if there is data in the FIFO."),
                CSRField("pend", description="``1`` if there is an IRQ pending."),
            ],
            description="Status about the current state of the `OUT` endpoint."
        )

        self.submodules.ev = ev.EventManager()
        self.ev.submodules.packet = ev.EventSourcePulse(name="done", description="""
            Indicates that an ``OUT`` packet has successfully been transferred
            from the host.  This bit must be cleared in order to receive
            additional packets.""")
        self.ev.finalize()

        self.usb_reset = Signal()

        self.stalled = Signal()
        self.enabled = Signal()
        stall_status = Signal(16)
        enable_status = Signal(16)
        ep_mask = Signal(16, reset=1)

        if cdc:
            setup_sys = Signal()
            commit_sys = Signal()
            endp_sys = Signal(4)
            self.specials += MultiReg(usb_core.setup, setup_sys)
            self.specials += MultiReg(usb_core.commit, commit_sys)
            self.submodules.endpsync = BusSynchronizer(4, "usb_12", "sys")
            self.comb += [
                self.endpsync.i.eq(usb_core.endp),
                endp_sys.eq(self.endpsync.o),
            ]
            self.comb += [
                If(setup_sys | commit_sys,
                    ep_mask.eq(1 << endp_sys),
                ).Else(
                    ep_mask.eq(1 << ctrl.fields.epno),
                ),
            ]
            self.specials += MultiReg(stall_status >> endp_sys, self.stalled, "usb_12")
            self.specials += MultiReg(enable_status >> endp_sys, self.enabled, "usb_12")
            self.sync += [
                If(ctrl.fields.reset | self.usb_reset,
                    stall_status.eq(0),
                ).Elif(setup_sys | (ctrl.re & ~ctrl.fields.stall),
                    # If a SETUP packet comes in, clear the STALL bit.
                    stall_status.eq(stall_status & ~ep_mask),
                ).Elif(ctrl.re,
                    stall_status.eq(stall_status | ep_mask),
                ),
            ]
        else:
            self.comb += [
                If(usb_core.setup | usb_core.commit,
                    ep_mask.eq(1 << usb_core.endp),
                   ).Else(
                    ep_mask.eq(1 << ctrl.fields.epno),
                ),
                self.stalled.eq(stall_status >> usb_core.endp),
                self.enabled.eq(enable_status >> usb_core.endp),
            ]
            self.sync += [
                If(ctrl.fields.reset | self.usb_reset,
                    stall_status.eq(0),
                   ).Elif(usb_core.setup | (ctrl.re & ~ctrl.fields.stall),
                    # If a SETUP packet comes in, clear the STALL bit.
                    stall_status.eq(stall_status & ~ep_mask),
                          ).Elif(ctrl.re,
                    stall_status.eq(stall_status | ep_mask),
                ),
            ]

        # The endpoint number of the most recently received packet
        epno = Signal(4)

        # How to respond to requests:
        #  - 1 - ACK
        #  - 0 - NAK
        # Send a NAK if the buffer contains data, or if "ENABLE" has not been set.
        self.response = Signal()
        responding = Signal()
        is_out_packet = Signal()

        if cdc:
            poll_sys = Signal()
            self.specials += MultiReg(usb_core.poll, poll_sys)
            tok_sys = Signal(4)
            self.submodules.toksync = BusSynchronizer(4, "usb_12", "sys")
            self.comb += [
                self.toksync.i.eq(usb_core.tok),
                tok_sys.eq(self.toksync.o),
            ]
            # Keep track of whether we're currently responding.
            self.comb += is_out_packet.eq(tok_sys == PID.OUT)

            #self.specials += MultiReg(self.enabled & is_out_packet & ~self.ev.packet.pending, self.response, "usb_12")
            enabled_12 = Signal()
            pending_12 = Signal()
            self.specials += MultiReg(self.enabled, enabled_12)
            self.specials += MultiReg(self.ev.packet.pending, pending_12)
            self.comb += self.response.eq(enabled_12 & (usb_core.tok == PID.OUT) & ~pending_12)  # in usb_12 domain, usb_core.tok -> response path is critical

            response_sys = Signal()
            self.comb += response_sys.eq(self.enabled & is_out_packet & ~self.ev.packet.pending)
            responding12 = Signal()
            responding_reset = Signal()
            responding_reset12 = Signal()
            self.specials += MultiReg(responding_reset, responding_reset12, "usb_12")
            self.sync.usb_12 += \
                If(responding_reset12,
                    responding12.eq(0)
                ).Else(
                    If(usb_core.poll, responding12.eq(self.response)),
                )
            self.specials += MultiReg(responding12, responding)
        else:
            # Keep track of whether we're currently responding.
            self.comb += is_out_packet.eq(usb_core.tok == PID.OUT)
            self.comb += self.response.eq(self.enabled & is_out_packet & ~self.ev.packet.pending)
            self.sync += If(usb_core.poll, responding.eq(self.response))

        # Connect the buffer to the USB system
        self.data_recv_payload = Signal(8)
        self.data_recv_put = Signal()
        if cdc:
            self.submodules.bufressync = BlindTransfer("sys", "usb_12")
            self.comb += [
                buf.reset_sys.eq(ctrl.fields.reset),
                self.bufressync.i.eq(ctrl.fields.reset),
                buf.reset_usb_12.eq(self.bufressync.o),
            ]

            self.comb += buf.re.eq(data.we)          # When data is read, advance the FIFO

            # work around async buffer readable-X issue
            self.comb += self.status.fields.have.eq(buf.readable)
            data_reg = Signal(8)
            self.sync += [
                If(buf.readable,
                    data_reg.eq(buf.dout)
                ).Else(
                    data_reg.eq(0)
                )
            ]

            self.comb += [
                self.data.fields.data.eq(data_reg),
            ]
            self.comb += [
                buf.din.eq(self.data_recv_payload),
                buf.we.eq(self.data_recv_put & responding12),

                self.status.fields.epno.eq(epno),
                self.status.fields.pend.eq(self.ev.packet.pending),

                # When data is successfully transferred, the buffer becomes full.
                # This is true even if "no" data was transferred, because the
                # buffer will then contain two bytes of CRC16 data.
                # Therefore, if the FIFO is readable, an interrupt must be triggered.
                self.ev.packet.trigger.eq(responding & commit_sys),
            ]

            # If we get a packet, turn off the "IDLE" flag and keep it off until the packet has finished.
            self.sync += [
                If(ctrl.fields.reset,
                    enable_status.eq(0),
                    responding_reset.eq(0),
                ).Elif(commit_sys & responding,
                    epno.eq(endp_sys),
                    # Disable this EP when a transfer finishes
                    enable_status.eq(enable_status & ~ep_mask),
                    responding_reset.eq(1),
                ).Elif(ctrl.re,
                    responding_reset.eq(0),
                    # Enable or disable the EP as necessary
                    If(ctrl.fields.enable,
                        enable_status.eq(enable_status | ep_mask),
                    ).Else(
                        enable_status.eq(enable_status & ~ep_mask),
                    ),
                ),
            ]
        else:
            self.comb += [
                buf.din.eq(self.data_recv_payload),
                buf.we.eq(self.data_recv_put & responding),
                buf.reset.eq(ctrl.fields.reset),
                self.data.fields.data.eq(buf.dout),

                # When data is read, advance the FIFO
                buf.re.eq(data.we),

                self.status.fields.epno.eq(epno),
                self.status.fields.have.eq(buf.readable),
                self.status.fields.pend.eq(self.ev.packet.pending),

                # When data is successfully transferred, the buffer becomes full.
                # This is true even if "no" data was transferred, because the
                # buffer will then contain two bytes of CRC16 data.
                # Therefore, if the FIFO is readable, an interrupt must be triggered.
                self.ev.packet.trigger.eq(responding & usb_core.commit),
            ]

            # If we get a packet, turn off the "IDLE" flag and keep it off until the packet has finished.
            self.sync += [
                If(ctrl.fields.reset,
                    enable_status.eq(0),
                ).Elif(usb_core.commit & responding,
                    epno.eq(usb_core.endp),
                    # Disable this EP when a transfer finishes
                    enable_status.eq(enable_status & ~ep_mask),
                    responding.eq(0),
                       ).Elif(ctrl.re,
                    # Enable or disable the EP as necessary
                    If(ctrl.fields.enable,
                        enable_status.eq(enable_status | ep_mask),
                    ).Else(
                        enable_status.eq(enable_status & ~ep_mask),
                    ),
                ),
            ]

        # These are useful for debugging
        # self.enable_status = CSRStatus(8, description)
        # self.comb += self.enable_status.status.eq(enable_status)
        # self.stall_status = CSRStatus(8)
        # self.comb += self.stall_status.status.eq(stall_status)
