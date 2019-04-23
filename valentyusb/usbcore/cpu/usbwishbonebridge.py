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
            If(cmd_ce, cmd.eq(usb_core.data_recv_payload[7:8])),
            If(address_ce, address.eq(Cat(address[8:32], usb_core.data_recv_payload))),
            If(rx_data_ce,
                data.eq(Cat(data[8:32], usb_core.data_recv_payload))
            ).Elif(tx_data_ce,
                data.eq(self.wishbone.dat_r)
            )
        ]


        fsm = ResetInserter()(FSM(reset_state="IDLE"))
        self.submodules += fsm
        fsm.act("IDLE",
            self.n_debug_in_progress.eq(1),
            If(usb_core.data_recv_put,
                If(usb_core.tok == PID.SETUP,
                    If(usb_core.endp == 0,
                        # If we get a SETUP packet with a "Vendor" type
                        # going to this device, treat that as a DEBUG packet.
                        If(usb_core.data_recv_payload[0:7] == 0x40,
                            cmd_ce.eq(1),
                            NextState("RECEIVE_ADDRESS"),
                        ).Else(
                            # Wait for the end of the packet, to avoid
                            # messing with normal USB operation
                            NextState("WAIT_PKT_END"),
                        ),
                        byte_counter_reset.eq(1),
                    )
                )
            )
        )

        # The target address comes as the wValue and wIndex in the SETUP
        # packet.  Once we get that data, we're ready to do the operation.
        fsm.act("RECEIVE_ADDRESS",
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
                    NextState("READ_DATA")
                ).Else(
                    NextState("RECEIVE_DATA")
                ),
            ),
        )

        fsm.act("RECEIVE_DATA",
            # Set the "ACK" bit to 1, so we acknowledge the packet
            # once it comes in, and so that we're in a position to
            # receive data.
            self.send_ack.eq(1),
            If(usb_core.data_recv_put,
                rx_data_ce.eq(1),
                byte_counter_ce.eq(1),
                If(byte_counter == 3,
                    NextState("WAIT_RECEIVE_DATA_END"),
                    byte_counter_reset.eq(1)
                ).Elif(usb_core.end,
                    NextState("WRITE_DATA"),
                    byte_counter_reset.eq(1)
                )
            )
        )
        fsm.act("WAIT_RECEIVE_DATA_END",
            self.send_ack.eq(1),
            # Wait for the end of the USB packet, if
            # it hasn't come already.
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
            byte_counter_reset.eq(1),
            self.wishbone.stb.eq(1),
            self.wishbone.we.eq(1),
            self.wishbone.cyc.eq(1),
            If(self.wishbone.ack | self.wishbone.err,
                NextState("WAIT_SEND_ACK_START"),
            )
        )

        fsm.act("READ_DATA",
            byte_counter_reset.eq(1),
            self.wishbone.stb.eq(1),
            self.wishbone.we.eq(0),
            self.wishbone.cyc.eq(1),
            If(self.wishbone.ack | self.wishbone.err,
                tx_data_ce.eq(1),
                NextState("SEND_DATA_WAIT_START")
            )
        )

        fsm.act("SEND_DATA_WAIT_END",
            If(usb_core.end,
                NextState("SEND_DATA"),
            ),
        )
        fsm.act("SEND_DATA_WAIT_START",
            byte_counter_reset.eq(1),
            If(usb_core.start,
                NextState("SEND_DATA"),
            ),
        )
        self.comb += \
            chooser(data, byte_counter, self.sink_data, n=4, reverse=False)
        fsm.act("SEND_DATA",
            If(usb_core.endp == 0,
                # Keep sink_valid high during the packet, which indicates we have data
                # to send.  This also causes an "ACK" to be transmitted.
                self.sink_valid.eq(1),
                If(usb_core.data_send_get,
                    byte_counter_ce.eq(1),
                ),
                If(byte_counter == 4,
                    NextState("WAIT_SEND_ACK_START")
                ),
                If(usb_core.end,
                    NextState("WAIT_SEND_ACK_START")
                )
            ).Else(
                NextState("SEND_DATA_WAIT_START"),
            )
        )

        # To validate the transaction was successful, the host will now
        # send an "IN" request.  Acknowledge that by setting
        # self.send_ack, without putting anything in self.sink_data.
        fsm.act("WAIT_SEND_ACK_START",
            If(usb_core.start,
                NextState("WAIT_PKT_END_DBG"),
            )
        )
        fsm.act("WAIT_PKT_END_DBG",
            If(usb_core.endp == 0,
                self.send_ack.eq(1),
                If(usb_core.end,
                    NextState("IDLE"),
                )
            ).Else(
                NextState("WAIT_SEND_ACK_START")
            )
        )

        fsm.act("WAIT_PKT_END",
            self.n_debug_in_progress.eq(1),
            If(usb_core.end,
                NextState("IDLE"),
            )
        )

        timer = WaitTimer(clk_freq//1000)
        self.submodules += timer
        self.comb += [
            fsm.reset.eq(timer.done),
            timer.wait.eq(~fsm.ongoing("IDLE"))
        ]