from migen import *

from migen.genlib.misc import chooser, WaitTimer
from migen.genlib.record import Record
from migen.genlib.fsm import FSM, NextState
from migen.genlib.fifo import AsyncFIFOBuffered
from migen.genlib.cdc import PulseSynchronizer, MultiReg, BusSynchronizer
from litex.soc.interconnect import stream

from litex.soc.interconnect import wishbone
from litex.soc.interconnect import stream

from litex.soc.integration.doc import ModuleDoc, AutoDoc

from ..pid import PID, PIDTypes

class USBWishboneBurstBridge(Module, AutoDoc):

    def __init__(self, usb_core, magic_packet=0x43):
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

        # Unlike the UART or Ethernet bridges, we explicitly only
        # support two commands: reading and writing.  This gets
        # integrated into the USB protocol, so it's not really a
        # state.  1 is "USB Device to Host", and is therefore a "read",
        # while 0 is "USB Host to Device", and is therefore a "write".
        self.cmd = cmd = Signal(1, reset_less=True)
        cmd_ce = Signal()

        self.data_phase = Signal()

        # Instead of self.source and self.sink, we let the wrapping
        # module handle packing and unpacking the data.
        self.sink_data = Signal(8)

        # True when the "sink" value has data
        self.sink_valid = Signal()

        self.send_ack = Signal()

        # Indicates whether a "debug" packet is currently being processed
        self.n_debug_in_progress = Signal(reset=1)


        byte_counter = Signal(17, reset_less=True) # up to 64k + 1
        byte_counter_reset = Signal()
        byte_counter_ce = Signal()
        self.sync.usb_12 += \
            If(byte_counter_reset,
                byte_counter.eq(0)
            ).Elif(byte_counter_ce,
                byte_counter.eq(byte_counter + 1)
            )

        burst_counter = Signal(7, reset_less=True) # up to 64 + 1
        burst_counter_ce = Signal()
        self.sync.usb_12 += \
            If(usb_core.start,
                burst_counter.eq(0)
            ).Elif(burst_counter_ce,
                burst_counter.eq(burst_counter + 1)
            )

        self.address = address = Signal(32, reset_less=True)
        address_ce = Signal()
        address_inc = Signal()

        self.length = length = Signal(16, reset_less=True)
        length_ce = Signal()

        self.data = data = Signal(32)
        self.rd_data = Signal(32)
        rx_data_ce = Signal()

        # wishbone_response = Signal(32, reset_less=True)
        self.sync.usb_12 += [
            If(cmd_ce, cmd.eq(usb_core.data_recv_payload[7:8])),
            If(address_ce,
                address.eq(Cat(address[8:32], usb_core.data_recv_payload)),
            ),#.Elif(address_inc,
            #    address.eq(address + 4),
            #),
            If(length_ce, length.eq(Cat(length[8:16],usb_core.data_recv_payload))),
            If(rx_data_ce,
                data.eq(Cat(data[8:32], usb_core.data_recv_payload))
            )
        ]

        # Add a bridge to allow this module (in the usb_12 domain) to access
        # the main Wishbone bridge (potentially in some other domain).
        # Ensure this bridge is placed in the "sys" domain.
        self.cmd_sys = cmd_sys = Signal()
        self.specials += MultiReg(cmd, cmd_sys)
        prefetch_go = Signal()
        prefetch_go_sys = Signal()
        self.specials += MultiReg(prefetch_go, prefetch_go_sys)

        ### cross clock domains using a FIFO. also makes burst access possible.
        self.submodules.write_fifo = ClockDomainsRenamer({"write": "usb_12", "read": "sys"})(AsyncFIFOBuffered(width=32, depth=64//4))
        self.submodules.read_fifo = ClockDomainsRenamer({"write": "sys", "read": "usb_12"})(AsyncFIFOBuffered(width=32, depth=64//4))
        self.comb += [
            # clk12 domain
            self.write_fifo.din.eq(data),      # data coming from USB interface
            self.rd_data.eq(self.read_fifo.dout),  # data going to USB interface
            # sys domain
            self.read_fifo.din.eq(self.wishbone.dat_r),
            self.wishbone.dat_w.eq(self.write_fifo.dout),
        ]

        self.submodules.address_synchronizer = BusSynchronizer(32, "usb_12", "sys")
        self.comb += self.address_synchronizer.i.eq(self.address),
        self.submodules.length_synchronizer = BusSynchronizer(16, "usb_12", "sys")
        self.length_sys = Signal(16)
        self.comb += [self.length_synchronizer.i.eq(self.length), self.length_sys.eq(self.length_synchronizer.o)]

        self.burstcount = Signal(16)
        addr_to_wb = Signal(32)
        self.comb += [
            addr_to_wb.eq(self.address_synchronizer.o + self.burstcount),
            self.wishbone.adr.eq(addr_to_wb[2:])
        ]
        wbmanager = FSM(reset_state="IDLE") # in sys domain
        self.submodules += wbmanager
        wbmanager.act("IDLE",
            NextValue(self.burstcount, 0),
            If(prefetch_go_sys & cmd_sys,  # 0xC3 (bit set) == read
                NextState("READER")
            ).Elif(prefetch_go_sys & ~cmd_sys,
                NextState("WRITER")
            ),
            If(self.write_fifo.readable, # clear entries in write fifo in case of e.g. error condition or previous abort
                self.write_fifo.re.eq(1),
            )
        )
        wbmanager.act("READER",
            If(self.burstcount < self.length_sys,
                If(self.read_fifo.writable,
                    self.wishbone.stb.eq(1),
                    self.wishbone.we.eq(0),
                    self.wishbone.cyc.eq(1),
                    self.wishbone.cti.eq(0),  # classic cycle
                    NextState("READER_WAIT")
                )
            ).Else(
                NextState("WAIT_DONE")
            )
        )
        wbmanager.act("READER_WAIT",
            self.wishbone.stb.eq(1),
            self.wishbone.we.eq(0),
            self.wishbone.cyc.eq(1),
            self.wishbone.cti.eq(0),  # classic cycle

            If(self.wishbone.ack | self.wishbone.err,
                self.read_fifo.we.eq(1),
                NextValue(self.burstcount, self.burstcount + 4),
                NextState("READER"),
            )
        )
        wbmanager.act("WRITER",
            If(self.burstcount < self.length_sys,
                If(self.write_fifo.readable,
                    self.wishbone.stb.eq(1),
                    self.wishbone.we.eq(1),
                    self.wishbone.cyc.eq(1),
                    self.wishbone.cti.eq(0),  # classic cycle
                    NextState("WRITER_WAIT"),
                )
            ).Else(
                NextState("WAIT_DONE")
            )
        )
        wbmanager.act("WRITER_WAIT",
            self.wishbone.stb.eq(1),
            self.wishbone.we.eq(1),
            self.wishbone.cyc.eq(1),
            self.wishbone.cti.eq(0),  # classic cycle
                      
            If(self.wishbone.ack | self.wishbone.err,
                self.write_fifo.re.eq(1),
                NextValue(self.burstcount, self.burstcount + 4),
                NextState("WRITER")
            )
        )
        wbmanager.act("WAIT_DONE",
            If(~prefetch_go_sys,
                NextState("IDLE")
            )
        )
        self.comb += self.wishbone.sel.eq(2 ** len(self.wishbone.sel) - 1)



        not_first_byte=Signal()
        
        fsm = ResetInserter()(ClockDomainsRenamer("usb_12")(FSM(reset_state="IDLE")))
        self.submodules += fsm
        fsm.act("IDLE",
            NextValue(prefetch_go, 0),
            NextValue(not_first_byte, 0),
            NextValue(self.data_phase, 0),
            self.n_debug_in_progress.eq(1),
            # drain any excess entries in read FIFO, in case we are recovering from e.g. an error condition
            If(self.read_fifo.readable,
                self.read_fifo.re.eq(1),
            ),
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
                    ).Elif((byte_counter <= 6),
                        length_ce.eq(1),
                    )
                ),
            ),
            If(byte_counter == 7, # length is stable, can start prefetching now
                NextValue(prefetch_go, 1),
            ),
            # We don't need to explicitly ACK the SETUP packet, because
            # they're always acknowledged implicitly.  Wait until the
            # packet ends (i.e. until we've sent the ACK packet) before
            # moving to the next state.
            If(usb_core.end,
                byte_counter_reset.eq(1),
                If(cmd,
                    NextState("READ_DATA"),
                ).Else(
                    NextState("RECEIVE_DATA"),
                ),
            ),
        )

        #################### WRITE MACHINE

        fsm.act("RECEIVE_DATA",
            # Set the "ACK" bit to 1, so we acknowledge the packet
            # once it comes in, and so that we're in a position to
            # receive data.
            self.send_ack.eq(usb_core.endp == 0),
            self.n_debug_in_progress.eq(0),
            If(usb_core.endp == 0,
                If(usb_core.data_recv_put,
                    rx_data_ce.eq(1),
                    If(burst_counter < 64,
                       byte_counter_ce.eq(1),
                    ),
                    burst_counter_ce.eq(1),
                    If((burst_counter <= 64) & ((burst_counter & 3) == 0) & (burst_counter != 0),
                       self.write_fifo.we.eq(1),
                       address_inc.eq(1),
                    ),
                    If(byte_counter == (length - 1) | (((byte_counter & 0x3F) == 0x3F) & not_first_byte),
                        NextState("WAIT_RECEIVE_DATA_END"),
                    ).Elif(usb_core.end,
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
                self.write_fifo.we.eq(1),
                NextState("WRITE_DATA")
            )
        )

        fsm.act("WRITE_DATA",
            self.n_debug_in_progress.eq(0),
            If(self.write_fifo.writable,
                NextState("WAIT_SEND_ACK_START_WRITE"),
            )
        )

        fsm.act("WAIT_SEND_ACK_START_WRITE",
            self.n_debug_in_progress.eq(0),
            If(usb_core.start,
               NextState("SEND_ACK_WRITE")
            ),
        )

        # Send the ACK.  If the endpoint number is incorrect, go back and
        # wait again.
        fsm.act("SEND_ACK_WRITE",
            self.n_debug_in_progress.eq(0),
            If(usb_core.endp != 0,
                NextState("WAIT_SEND_ACK_START")
            ),
            self.send_ack.eq(usb_core.endp == 0),
            If(usb_core.end,
               If( byte_counter != length, 
                   NextValue(not_first_byte, 0),
                   NextValue(self.data_phase, ~self.data_phase),
                   NextState("RECEIVE_DATA"),
                ).Else(
                   NextState("IDLE"),
                )
            ),
        )

        ############### READ MACHINE

        fsm.act("READ_DATA",
            self.n_debug_in_progress.eq(0),
            If(self.read_fifo.readable,
                NextState("SEND_DATA_WAIT_START"),
            )
        )

        fsm.act("SEND_DATA_WAIT_START",
            self.n_debug_in_progress.eq(0),
            If(usb_core.start,
                NextState("SEND_DATA"),
            ),
        )
        fsm.act("SEND_DATA_BURST_WAIT",
            self.n_debug_in_progress.eq(0),
            self.sink_valid.eq(usb_core.endp == 0),
            If(self.read_fifo.readable,
               NextState("SEND_DATA"),
            )
        )
        self.sync.usb_12 += \
            chooser(self.rd_data, byte_counter[0:2], self.sink_data, n=4, reverse=False)
        fsm.act("SEND_DATA",
            self.n_debug_in_progress.eq(0),
            If(usb_core.endp != 0,
                NextState("SEND_DATA_WAIT_START"),
            ),
            # Keep sink_valid high during the packet, which indicates we have data
            # to send.  This also causes an "ACK" to be transmitted.
            self.sink_valid.eq(usb_core.endp == 0),
            If(usb_core.data_send_get,
                NextValue(not_first_byte, 1),
                byte_counter_ce.eq(1),
                If( ((byte_counter & 3) == 3) & ((byte_counter + 1) != length),
                    self.read_fifo.re.eq(1), # advance the read fifo by one position
                    address_inc.eq(1),
                    NextState("SEND_DATA_BURST_WAIT"),
                )
            ),
            If( (byte_counter == length) | (((byte_counter & 0x3F) == 0x00) & not_first_byte),
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
            If(usb_core.end & (byte_counter != length),
               #byte_counter_ce.eq(1),
                #address_inc.eq(1),
               NextValue(not_first_byte, 0),
               NextValue(self.data_phase, ~self.data_phase),
               NextState("READ_DATA"),
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
                self.read_fifo.re.eq(1), # drain the last entry in the read fifo
                NextState("IDLE"),
            )
        )

        fsm.act("WAIT_PKT_END",
            self.n_debug_in_progress.eq(1),
            If(usb_core.end,
                NextState("IDLE"),
            )
        )
