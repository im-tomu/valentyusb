#!/usr/bin/env python3

from enum import IntEnum

from migen import *
from migen.genlib import fsm

from litex.soc.integration.doc import AutoDoc, ModuleDoc

from ..endpoint import EndpointType, EndpointResponse
from ..pid import PID, PIDTypes
from ..sm.transfer import UsbTransfer
from .usbwishbonebridge import USBWishboneBridge

class DummyUsb(Module, AutoDoc, ModuleDoc):
    """DummyUSB Self-Enumerating USB Controller

    This implements a device that simply responds to the most common SETUP packets.
    It is intended to be used alongside the Wishbone debug bridge.
    """

    def __init__(self, iobuf, debug=False, vid=0x1209, pid=0x5bf0,
        product="Fomu Bridge",
        manufacturer="Foosn",
        cdc=False):
        """
        Arguments:

        cdc: True if the Wishbone bus isn't in the same clock domain
        as `usb_12`, then insert a clock domain crossing construct.
        """
        # USB Core
        self.submodules.usb_core = usb_core = UsbTransfer(iobuf)
        if usb_core.iobuf.usb_pullup is not None:
            self.comb += usb_core.iobuf.usb_pullup.eq(1)
        self.iobuf = usb_core.iobuf

        # SETUP packets contain a DATA segment that is always 8 bytes.
        # However, we're only ever interested in the first 4 bytes, plus
        # the last byte.
        usbPacket = Signal(32)
        wRequestAndType = Signal(16)
        wValue = Signal(16)
        wLength = Signal(8)
        self.comb += [
            wRequestAndType.eq(usbPacket[16:32]),
            wValue.eq(usbPacket[0:16]),
        ]
        setup_index = Signal(4)

        address = Signal(7, reset=0)
        self.comb += usb_core.addr.eq(address),

        def make_usbstr(s):
            usbstr = bytearray(2)
            # The first byte is the number of characters in the string.
            # Because strings are utf_16_le, each character is two-bytes.
            # That leaves 126 bytes as the maximum length
            assert(len(s) <= 126)
            usbstr[0] = (len(s)*2)+2
            usbstr[1] = 3
            usbstr.extend(bytes(s, 'utf_16_le'))
            return list(usbstr)

        # Start with 0x8006
        descriptors = {
            # Config descriptor
            # 80 06 00 02
            0x0002: [
                0x09, 0x02, 0x12, 0x00, 0x01, 0x01, 0x01, 0x80,
                0x32, 0x09, 0x04, 0x00, 0x00, 0x00, 0xfe, 0x00,
                0x00, 0x02,
            ],

            # Device descriptor
            # 80 06 00 01
            0x0001: [
                0x12, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x40,
                (vid>>0)&0xff, (vid>>8)&0xff,
                (pid>>0)&0xff, (pid>>8)&0xff,
                0x01, 0x01, 0x01, 0x02,
                0x00, 0x01,
            ],

            # String 0
            0x0003: [
                0x04, 0x03, 0x09, 0x04,
            ],

            # String 1 (manufacturer)
            0x0103: make_usbstr(manufacturer),

            # String 2 (Product)
            0x0203: make_usbstr(product),

            # BOS descriptor
            0x000f: [
                0x05, 0x0f, 0x1d, 0x00, 0x01, 0x18, 0x10, 0x05,
                0x00, 0x38, 0xb6, 0x08, 0x34, 0xa9, 0x09, 0xa0,
                0x47, 0x8b, 0xfd, 0xa0, 0x76, 0x88, 0x15, 0xb6,
                0x65, 0x00, 0x01, 0x02, 0x01,
            ],

            0xee03: [
                0x12, 0x03, 0x4d, 0x53, 0x46, 0x54, 0x31, 0x30,
                0x30, 0x7e, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
                0x00, 0x00,
            ],
        }

        # Starts with 0xc07e or 0xc17e
        usb_ms_compat_id_descriptor = [
            0x28, 0x00, 0x00, 0x00, 0x00, 0x01, 0x04, 0x00,
            0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x01, 0x57, 0x49, 0x4e, 0x55, 0x53, 0x42,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        ]

        class MemoryContents:
            def __init__(self):
                self.contents = [0x00]
                self.offsets = {}
                self.lengths = {}

            def add(self, wRequestAndType, wValue, mem):
                self.offsets[wRequestAndType << 16 | wValue] = len(self.contents)
                self.lengths[wRequestAndType << 16 | wValue] = len(mem)
                self.contents = self.contents + mem

        mem = MemoryContents()
        for key, value in descriptors.items():
            mem.add(0x8006, key, value)

        mem.add(0xc07e, 0x0000, usb_ms_compat_id_descriptor)
        mem.add(0x8000, 0x0000, [0, 0]) # Get device status
        mem.add(0x0009, 0x0100, []) # Set configuration 1

        out_buffer = self.specials.out_buffer = Memory(8, len(mem.contents), init=mem.contents)
        self.specials.out_buffer_rd = out_buffer_rd = out_buffer.get_port(write_capable=False, clock_domain="usb_12")

        last_start = Signal()

        # Set to 1 if we have a response that matches the requested descriptor
        have_response = self.have_response = Signal()

        # Needs to be able to index Memory
        response_addr = Signal(9)
        response_len = Signal(6)
        response_ack = Signal()
        bytes_remaining = Signal(6)
        bytes_addr = Signal(9)

        # Respond to various descriptor requests
        cases = {}
        for key in mem.offsets:
            cases[key] = [
                response_len.eq(mem.lengths[key]),
                response_addr.eq(mem.offsets[key]),
            ]
        self.comb += Case(usbPacket, cases)

        # Used to respond to Transaction stage
        transaction_queued = Signal()
        new_address = Signal(7)
        configuration = Signal(8)

        # Generate debug signals, in case debug is enabled.
        debug_packet_detected = Signal()
        debug_sink_data = Signal(8)
        debug_sink_data_ready = Signal()
        debug_ack_response = Signal()

        # Delay the "put" signal (and corresponding data) by one cycle, to allow
        # the debug system to inhibit this write.  In practice, this doesn't
        # impact our latency at all as this signal runs at a rate of ~1 MHz.
        data_recv_put_delayed = self.data_recv_put_delayed = Signal()
        data_recv_payload_delayed = self.data_recv_payload_delayed = Signal(8)
        self.sync.usb_12 += [
            data_recv_put_delayed.eq(usb_core.data_recv_put),
            data_recv_payload_delayed.eq(usb_core.data_recv_payload),
        ]

        # Wire up debug signals if required
        if debug:
            debug_bridge = USBWishboneBridge(usb_core, cdc=cdc)
            self.submodules.debug_bridge = debug_bridge
            self.comb += [
                debug_packet_detected.eq(~self.debug_bridge.n_debug_in_progress),
                debug_sink_data.eq(self.debug_bridge.sink_data),
                debug_sink_data_ready.eq(self.debug_bridge.sink_valid),
                debug_ack_response.eq(self.debug_bridge.send_ack | self.debug_bridge.sink_valid),
            ]

        self.comb += [
            usb_core.dtb.eq(1),
            If(debug_packet_detected,
                usb_core.sta.eq(0),
                usb_core.arm.eq(debug_ack_response),
                usb_core.data_send_payload.eq(debug_sink_data),
                have_response.eq(debug_sink_data_ready),
            ).Else(
                usb_core.sta.eq(~(have_response | response_ack)),
                usb_core.arm.eq(have_response | response_ack),
                usb_core.data_send_payload.eq(out_buffer_rd.dat_r),
                have_response.eq(bytes_remaining > 0),
            ),
            out_buffer_rd.adr.eq(bytes_addr),
            usb_core.data_send_have.eq(have_response),
        ]

        self.sync.usb_12 += [
            usb_core.reset.eq(usb_core.error),
            last_start.eq(usb_core.start),
            If(usb_core.usb_reset,
                address.eq(0),
            ),
            If(last_start,
                If(usb_core.tok == PID.SETUP,
                    setup_index.eq(0),
                    bytes_remaining.eq(0),
                ).Elif(transaction_queued,
                    response_ack.eq(1),
                    transaction_queued.eq(0),
                    address.eq(new_address),
                )
            ),
            If(usb_core.tok == PID.SETUP,
                If(data_recv_put_delayed,
                    setup_index.eq(setup_index + 1),
                    Case(setup_index, {
                        0: usbPacket.eq(Cat(data_recv_payload_delayed, usbPacket[0:24], )),
                        1: usbPacket.eq(Cat(data_recv_payload_delayed, usbPacket[0:24], )),
                        2: usbPacket.eq(Cat(data_recv_payload_delayed, usbPacket[0:24], )),
                        3: usbPacket.eq(Cat(data_recv_payload_delayed, usbPacket[0:24], )),
                        # 4: wIndex.eq(data_recv_payload_delayed),
                        # 5: wIndex.eq(Cat(wIndex[0:8], data_recv_payload_delayed)),
                        6: wLength.eq(data_recv_payload_delayed),
                        # 7: wLength.eq(Cat(wLength[0:8], data_recv_payload_delayed)),
                    }),
                ),
            ),

            # After a SETUP's DATA packet has come in, figure out if we need
            # to respond to any special requests.
            If(usb_core.setup,
                # Set Address / Configuration
                If(wRequestAndType == 0x0005,
                    # Set Address
                    new_address.eq(wValue[8:15]),
                    response_ack.eq(1),
                ).Elif(wRequestAndType == 0x0009,
                    configuration.eq(wValue[8:15]),
                    response_ack.eq(1),
                ),
                If(response_len > wLength,
                    bytes_remaining.eq(wLength),
                ).Else(
                    bytes_remaining.eq(response_len),
                ),
                bytes_addr.eq(response_addr),
            ),

            If(usb_core.data_send_get,
                response_ack.eq(1),
                bytes_addr.eq(bytes_addr + 1),
                If(bytes_remaining,
                    bytes_remaining.eq(bytes_remaining - 1),
                ),
            ),
            If(self.data_recv_put_delayed,
                response_ack.eq(0),
                transaction_queued.eq(1),
            ),
        ]
