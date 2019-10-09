from migen import *

from migen.genlib.misc import chooser, WaitTimer
from migen.genlib.record import Record
from migen.genlib.fsm import FSM, NextState
from migen.genlib.fifo import AsyncFIFOBuffered
from migen.genlib.cdc import PulseSynchronizer
from litex.soc.interconnect import stream

from litex.soc.interconnect import wishbone
from litex.soc.interconnect import stream

from litex.soc.integration.doc import ModuleDoc, AutoDoc

from ..pid import PID, PIDTypes

class USBWishboneBridge(Module, AutoDoc):

    def __init__(self, usb_core, clk_freq=12000000, magic_packet=0x43, cdc=False):
        self.wishbone = wishbone.Interface()

        self.background = ModuleDoc(title="USB Wishbone Bridge", body="""
            This bridge provides a transparent bridge to the target device's Wishbone bus over USB.
            It can operate without interfering with the device's USB stack.  It is simple enough to
            be able to work even if the USB stack is not enumerated, though the host may not cooperate.""")

        self.protocol = ModuleDoc(title="USB Wishbone Debug Protocol", body="""
        The protocol transfers four bytes a time in big-endian (i.e. USB) order.  It uses SETUP packets
        with the special type (0x43) as an `attention` word.  This is then followed by an ``OUT`` packet.

            .. wavedrom::
                :caption: Write to Wishbone

                { "signal": [
                    ["Request",
                        {  "name": 'data',        "wave": 'x222...22x', "data": '0x43 0x00 [ADDRESS] 0x04 0x00'   },
                        {  "name": 'data bits',   "wave": 'xxx2222xxx', "data": '7:0 15:8 23:16 31:24'},
                        {  "name": 'usb meaning', "wave": 'x222.2.2.x', "data": 'bReq bTyp wValue wIndex wLength' },
                        {  "name": 'usb byte',    "wave": 'x22222222x', "data": '1 2 3 4 5 6 7 8'                 }
                    ],
                    {},
                    ["Payload",
                        {  "name": 'data',        "wave": 'x3...x', "data": '[DATA]'},
                        {  "name": 'data bits',   "wave": 'x3333x', "data": '7:0 15:8 23:16 31:24'},
                        {  "name": 'usb meaning', "wave": 'x3...x', "data": 'OUT'  },
                        {  "name": 'usb byte',    "wave": 'x3333x', "data": '1 2 3 4'}
                    ]
                ]}

        To read data from the device, set the top bit of the `bRequestType`, followed by an ``IN`` packet.

            .. wavedrom::
                :caption: Read from Wishbone

                { "signal": [
                    ['Request',
                        {  "name": 'data',        "wave": 'x222...22x', "data": '0xC3 0x00 [ADDRESS] 0x04 0x00'   },
                        {  "name": 'data bits',   "wave": 'xxx2222xxx', "data": '7:0 15:8 23:16 31:24'},
                        {  "name": 'usb meaning', "wave": 'x222.2.2.x', "data": 'bReq bTyp wValue wIndex wLength' },
                        {  "name": 'usb byte',    "wave": 'x22222222x', "data": '1 2 3 4 5 6 7 8'                 }
                    ],
                    {},
                    ["Payload",
                        {  "name": 'data',        "wave": 'x5...x', "data": '[DATA]'},
                        {  "name": 'data bits',   "wave": 'x5555x', "data": '7:0 15:8 23:16 31:24'},
                        {  "name": 'usb meaning', "wave": 'x5...x', "data": 'IN'  },
                        {  "name": 'usb byte',    "wave": 'x5555x', "data": '1 2 3 4'}
                    ]
                ]}
        """)
        # # #

        byte_counter = Signal(3, reset_less=True)
        byte_counter_reset = Signal()
        byte_counter_ce = Signal()
        self.sync.usb_12 += \
            If(byte_counter_reset,
                byte_counter.eq(0)
            ).Elif(byte_counter_ce,
                byte_counter.eq(byte_counter + 1)
            )

        # Unlike the UART or Ethernet bridges, we explicitly only
        # support two commands: reading and writing.  This gets
        # integrated into the USB protocol, so it's not really a
        # state.  1 is "USB Device to Host", and is therefore a "read",
        # while 0 is "USB Host to Device", and is therefore a "write".
        cmd = Signal(1, reset_less=True)
        cmd_ce = Signal()

        # Add a bridge to allow this module (in the usb_12 domain) to access
        # the main Wishbone bridge (potentially in some other domain).
        # Ensure this bridge is placed in the "sys" domain.
        send_to_wishbone = Signal()
        reply_from_wishbone = Signal()
        transfer_active = Signal()
        if cdc:
            self.submodules.wb_cd_bridge = wb_cd_bridge = FSM(reset_state="IDLE")
            self.submodules.usb_to_wb = usb_to_wb = PulseSynchronizer("usb_12", "sys")
            self.submodules.wb_to_uwb = wb_to_usb = PulseSynchronizer("sys", "usb_12")
            send_to_wishbone = usb_to_wb.i
            reply_from_wishbone = wb_to_usb.o
        else:
            self.comb += [
                If(send_to_wishbone | transfer_active,
                    self.wishbone.stb.eq(1),
                    self.wishbone.we.eq(~cmd),
                    self.wishbone.cyc.eq(1),
                ),
                reply_from_wishbone.eq(self.wishbone.ack | self.wishbone.err),
            ]

        # Instead of self.source and self.sink, we let the wrapping
        # module handle packing and unpacking the data.
        self.sink_data = Signal(8)

        # True when the "sink" value has data
        self.sink_valid = Signal()

        self.send_ack = Signal()

        # Indicates whether a "debug" packet is currently being processed
        self.n_debug_in_progress = Signal(reset=1)

        address = Signal(32, reset_less=True)
        address_ce = Signal()

        data = Signal(32, reset_less=True)
        rd_data = Signal(32, reset_less=True)
        rx_data_ce = Signal()

        # wishbone_response = Signal(32, reset_less=True)
        self.sync.usb_12 += [
            If(cmd_ce, cmd.eq(usb_core.data_recv_payload[7:8])),
            If(address_ce, address.eq(Cat(address[8:32], usb_core.data_recv_payload))),
            If(rx_data_ce,
                data.eq(Cat(data[8:32], usb_core.data_recv_payload))
            )
        ]

        # The Litex Wishbone `dat_r` line is a shared medium, meaning the value
        # changes often.  Capture our own copy of this data when a wishbone ACK
        # occurs.
        self.sync.sys += [
            If(self.wishbone.ack,
                rd_data.eq(self.wishbone.dat_r)
            )
        ]


        fsm = ResetInserter()(ClockDomainsRenamer("usb_12")(FSM(reset_state="IDLE")))
        self.submodules += fsm
        fsm.act("IDLE",
            self.n_debug_in_progress.eq(1),
            If(usb_core.data_recv_put,
                If(usb_core.tok == PID.SETUP,
                    If(usb_core.endp == 0,
                        # If we get a SETUP packet with a "Vendor" type
                        # going to this device, treat that as a DEBUG packet.
                        cmd_ce.eq(1),
                        byte_counter_reset.eq(1),
                        If(usb_core.data_recv_payload[0:7] == magic_packet,
                            NextState("RECEIVE_ADDRESS"),
                        ).Else(
                            # Wait for the end of the packet, to avoid
                            # messing with normal USB operation
                            NextState("WAIT_PKT_END"),
                        ),
                    )
                )
            )
        )

        # The target address comes as the wValue and wIndex in the SETUP
        # packet.  Once we get that data, we're ready to do the operation.
        fsm.act("RECEIVE_ADDRESS",
            self.n_debug_in_progress.eq(0),
            If(usb_core.data_recv_put,
                byte_counter_ce.eq(1),
                If((byte_counter >= 1),
                    If((byte_counter <= 4),
                        address_ce.eq(1),
                    ),
                ),
            ),
            # We don't need to explicitly ACK the SETUP packet, because
            # they're always acknowledged implicitly.  Wait until the
            # packet ends (i.e. until we've sent the ACK packet) before
            # moving to the next state.
            If(usb_core.end,
                byte_counter_reset.eq(1),
                If(cmd,
                    send_to_wishbone.eq(1),
                    NextState("READ_DATA"),
                ).Else(
                    NextState("RECEIVE_DATA"),
                ),
            ),
        )

        fsm.act("RECEIVE_DATA",
            # Set the "ACK" bit to 1, so we acknowledge the packet
            # once it comes in, and so that we're in a position to
            # receive data.
            self.send_ack.eq(usb_core.endp == 0),
            self.n_debug_in_progress.eq(0),
            If(usb_core.endp == 0,
                If(usb_core.data_recv_put,
                    rx_data_ce.eq(1),
                    byte_counter_ce.eq(1),
                    If(byte_counter == 3,
                        NextState("WAIT_RECEIVE_DATA_END"),
                    ).Elif(usb_core.end,
                        send_to_wishbone.eq(1),
                        NextState("WRITE_DATA"),
                    )
                )
            )
        )
        fsm.act("WAIT_RECEIVE_DATA_END",
            self.n_debug_in_progress.eq(0),
            self.send_ack.eq(1),
            # Wait for the end of the USB packet, if
            # it hasn't come already.
            If(usb_core.end,
                send_to_wishbone.eq(1),
                NextState("WRITE_DATA")
            )
        )

        if cdc:
            wb_cd_bridge.act("IDLE",
                If(usb_to_wb.o,
                    NextState("DO_OP"),
                ),
            )
            wb_cd_bridge.act("DO_OP",
                self.wishbone.stb.eq(1),
                self.wishbone.we.eq(~cmd),
                self.wishbone.cyc.eq(1),
                If(self.wishbone.ack | self.wishbone.err,
                    NextState("IDLE"),
                    wb_to_usb.i.eq(1),
                ),
            )

        self.comb += [
            # Trim off the last two bits of the address, because wishbone addresses
            # are word-based, and a word is 32-bits.  Therefore, the last two bits
            # should always be zero.
            self.wishbone.adr.eq(address[2:]),
            self.wishbone.dat_w.eq(data),
            self.wishbone.sel.eq(2**len(self.wishbone.sel) - 1)
        ]
        fsm.act("WRITE_DATA",
            self.n_debug_in_progress.eq(0),
            transfer_active.eq(1),
            If(reply_from_wishbone,
                NextState("WAIT_SEND_ACK_START"),
            )
        )

        fsm.act("READ_DATA",
            self.n_debug_in_progress.eq(0),
            transfer_active.eq(1),
            If(reply_from_wishbone,
                NextState("SEND_DATA_WAIT_START")
            )
        )

        fsm.act("SEND_DATA_WAIT_START",
            self.n_debug_in_progress.eq(0),
            byte_counter_reset.eq(1),
            If(usb_core.start,
                NextState("SEND_DATA"),
            ),
        )
        self.comb += \
            chooser(rd_data, byte_counter, self.sink_data, n=4, reverse=False)
        fsm.act("SEND_DATA",
            self.n_debug_in_progress.eq(0),
            If(usb_core.endp != 0,
                NextState("SEND_DATA_WAIT_START"),
            ),

            # Keep sink_valid high during the packet, which indicates we have data
            # to send.  This also causes an "ACK" to be transmitted.
            self.sink_valid.eq(usb_core.endp == 0),
            If(usb_core.data_send_get,
                byte_counter_ce.eq(1),
            ),
            If(byte_counter == 4,
                NextState("WAIT_SEND_ACK_START")
            ),
            If(usb_core.end,
                NextState("WAIT_SEND_ACK_START")
            )
        )

        # To validate the transaction was successful, the host will now
        # send an "IN" request.  Acknowledge that by setting
        # self.send_ack, without putting anything in self.sink_data.
        fsm.act("WAIT_SEND_ACK_START",
            self.n_debug_in_progress.eq(0),
            If(usb_core.start,
                NextState("SEND_ACK")
            ),
        )

        # Send the ACK.  If the endpoint number is incorrect, go back and
        # wait again.
        fsm.act("SEND_ACK",
            self.n_debug_in_progress.eq(0),
            If(usb_core.endp != 0,
                NextState("WAIT_SEND_ACK_START")
            ),
            # If(usb_core.retry,
            #     If(cmd,
            #         byte_counter_reset.eq(1),
            #         NextState("SEND_DATA"),
            #     ),
            # ),
            self.send_ack.eq(usb_core.endp == 0),
            If(usb_core.end,
                NextState("IDLE"),
            )
        )

        fsm.act("WAIT_PKT_END",
            self.n_debug_in_progress.eq(1),
            If(usb_core.end,
                NextState("IDLE"),
            )
        )