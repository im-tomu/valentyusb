from migen import *

from migen.genlib.misc import chooser, WaitTimer
from migen.genlib.record import Record
from migen.genlib.fsm import FSM, NextState

from litex.soc.interconnect import wishbone
from litex.soc.interconnect import stream

from ..pid import PID, PIDTypes

class USBWishboneBridge(Module):

    def __init__(self, usb_core, clk_freq=12000000):
        self.wishbone = wishbone.Interface()

        length = 4 # Limit us to 4-byte writes

        # # #

        byte_counter = Signal(3, reset_less=True)
        byte_counter_reset = Signal()
        byte_counter_ce = Signal()
        self.sync += \
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

        # Instead of self.source and self.sink, we let the wrapping
        # module handle packing and unpacking the data.
        self.sink_data = Signal(8)

        # True when the "sink" value has data
        self.sink_valid = Signal()

        self.send_ack = Signal()

        # Indicates whether a "debug" packet is currently being processed
        self.n_debug_in_progress = Signal()

        address = Signal(32, reset_less=True)
        address_ce = Signal()

        data = Signal(32, reset_less=True)
        rx_data_ce = Signal()
        tx_data_ce = Signal()

        self.sync += [
            # If(cmd_ce, cmd.eq(usb_core.data_recv_payload[7:8])),
            # If(address_ce, address.eq(Cat(usb_core.data_recv_put, address[0:24]))),
            # If(rx_data_ce,
            #     data.eq(Cat(usb_core.data_recv_put, data[0:24]))
            # ).Elif(tx_data_ce,
            #     data.eq(self.wishbone.dat_r)
            # )
            If(cmd_ce, cmd.eq(usb_core.data_recv_payload[7:8])),
            If(address_ce, address.eq(Cat(address[8:32], usb_core.data_recv_payload))),
            If(rx_data_ce,
                data.eq(Cat(data[8:32], usb_core.data_recv_payload))
            ).Elif(tx_data_ce,
                data.eq(self.wishbone.dat_r)
            )
        ]


        # fsm = ResetInserter()(FSM(reset_state="IDLE"))
        fsm = FSM(reset_state="IDLE")
        timer = WaitTimer(clk_freq//10)
        self.submodules += fsm, timer
        # self.comb += [
        #     fsm.reset.eq(timer.done)
        # ]
        fsm.act("IDLE",
            self.n_debug_in_progress.eq(1),
            If(usb_core.data_recv_put,
                If(usb_core.tok == PID.SETUP,
                    # If we get a SETUP packet with a "Vendor" type
                    # going to this device, treat that as a DEBUG packet.
                    If(usb_core.data_recv_payload[0:7] == 0x40,
                        cmd_ce.eq(1),
                        NextState("RECEIVE_ADDRESS"),
                    )
                    # Otherwise, wait for the end of the packet, to avoid
                    # messing with normal USB operation
                    .Else(
                        NextState("WAIT_PKT_END"),
                    ),
                    byte_counter_reset.eq(1),
                )
            )
        )
        fsm.act("RECEIVE_ADDRESS",
            If(usb_core.data_recv_put,
                byte_counter_ce.eq(1),
                If((byte_counter >= 1),
                    If((byte_counter <= 4),
                        address_ce.eq(1),
                    ),
                ),
            ),
            If(usb_core.end,
                byte_counter_reset.eq(1),
                If(cmd,
                    NextState("READ_DATA")
                ).Else(
                    NextState("RECEIVE_DATA")
                ),
            ),
        )

        fsm.act("RECEIVE_DATA",
            If(usb_core.data_recv_put,
                rx_data_ce.eq(1),
                byte_counter_ce.eq(1),
                If(byte_counter == 3,
                    NextState("WAIT_RECEIVE_DATA_END"),
                    byte_counter_reset.eq(1)
                )
            )
        )
        fsm.act("WAIT_RECEIVE_DATA_END",
            self.send_ack.eq(1),
            If(usb_core.end,
                NextState("ACK_DATA_RECEIVED")
            )
        )
        fsm.act("ACK_DATA_RECEIVED",
            self.send_ack.eq(1),
            If(usb_core.end,
                NextState("ACK_DATA_RECEIVED"),
            ),
        )

        fsm.act("ACK_DATA_RECEIVED",
            self.send_ack.eq(1),
            If(usb_core.end,
                NextState("WRITE_DATA")
            )
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
            self.wishbone.stb.eq(1),
            self.wishbone.we.eq(1),
            self.wishbone.cyc.eq(1),
            If(self.wishbone.ack,
                NextState("WAIT_SEND_ACK"),
            )
        )
        fsm.act("WAIT_SEND_ACK",
            self.send_ack.eq(1),
            If(usb_core.end,
                NextState("WAIT_PKT_END_DBG"),
            ),
        )
        fsm.act("WAIT_PKT_2",
            self.send_ack.eq(1),
            If(usb_core.end,
                NextState("WAIT_PKT_END_DBG"),
            )
        )
        fsm.act("WAIT_PKT_3",
            self.send_ack.eq(1),
            If(usb_core.end,
                NextState("WAIT_PKT_END_DBG"),
            ),
        )

        fsm.act("READ_DATA",
            self.wishbone.stb.eq(1),
            self.wishbone.we.eq(0),
            self.wishbone.cyc.eq(1),
            If(self.wishbone.ack,
                tx_data_ce.eq(1),
                NextState("SEND_DATA")
            )
        )
        self.comb += \
            chooser(data, byte_counter, self.sink_data, n=4, reverse=True)
            # chooser(address, byte_counter, self.sink_data, n=4, reverse=True)
        fsm.act("SEND_DATA",
            self.sink_valid.eq(1),
            If(usb_core.data_send_get,
                byte_counter_ce.eq(1),
                If(byte_counter == 3,
                    NextState("WAIT_PKT_END_DBG")
                )
            )
        )

        fsm.act("WAIT_PKT_END_DBG",
            self.send_ack.eq(1),
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

        self.comb += timer.wait.eq(~fsm.ongoing("IDLE"))