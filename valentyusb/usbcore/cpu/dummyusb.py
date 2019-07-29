#!/usr/bin/env python3

from enum import IntEnum

from migen import *
from migen.genlib import fsm

from ..endpoint import EndpointType, EndpointResponse
from ..pid import PID, PIDTypes
from ..sm.transfer import UsbTransfer
from .usbwishbonebridge import USBWishboneBridge

class DummyUsb(Module):
    """

    Implements a device that simply responds to the most common SETUP packets.
    It is intended to be used alongside the Wishbone debug bridge.
    """

    def __init__(self, iobuf, debug=False, vid=0x1209, pid=0x5bf0,
        product="Fomu Bridge",
        manufacturer="Foosn"):
        # USB Core
        self.submodules.usb_core = usb_core = UsbTransfer(iobuf)
        if usb_core.iobuf.usb_pullup is not None:
            self.comb += usb_core.iobuf.usb_pullup.eq(1)
        self.iobuf = usb_core.iobuf

        # SETUP packets contain a DATA segment that is always 8 bytes
        # (for our purposes)
        bmRequestType = Signal(8)
        bRequest = Signal(8)
        wValue = Signal(16)
        wIndex = Signal(16)
        wLength = Signal(16)
        setup_index = Signal(4)

        def make_usbstr(s):
            usbstr = bytearray(2)
            # The first byte is the number of characters in the string.
            # Because strings are utf_16_le, each character is two-bytes.
            # That leaves 126 bytes as the maximum length
            assert(len(s) <= 126)
            usbstr[0] = len(s)*2
            usbstr[1] = 3
            usbstr.extend(bytes(s, 'utf_16_le'))
            return list(usbstr)

        # Allocate 64 bytes of transmit buffer, the only allowed size
        # for USB FS.
        usb_device_descriptor = [
            0x12, 0x01, 0x00, 0x02, 0x00, 0x00, 0x00, 0x40,
            (vid>>0)&0xff, (vid>>8)&0xff,
            (pid>>0)&0xff, (pid>>8)&0xff,
            0x01, 0x01, 0x01, 0x02,
            0x00, 0x01
        ]
        usb_config_descriptor = [
            0x09, 0x02, 0x12, 0x00, 0x01, 0x01, 0x01, 0x80,
            0x32, 0x09, 0x04, 0x00, 0x00, 0x00, 0xfe, 0x00,
            0x00, 0x02
        ]
        usb_string0_descriptor = [
            0x04, 0x03, 0x09, 0x04,
        ]

        usb_string_manufacturer = make_usbstr(manufacturer)
        usb_string_product = make_usbstr(product)

        usb_bos_descriptor = [
            0x05, 0x0f, 0x1d, 0x00, 0x01, 0x18, 0x10, 0x05,
            0x00, 0x38, 0xb6, 0x08, 0x34, 0xa9, 0x09, 0xa0,
            0x47, 0x8b, 0xfd, 0xa0, 0x76, 0x88, 0x15, 0xb6,
            0x65, 0x00, 0x01, 0x02, 0x01,
        ]
        usb_ms_compat_id_descriptor = [
            0x28, 0x00, 0x00, 0x00, 0x00, 0x01, 0x04, 0x00,
            0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x01, 0x57, 0x49, 0x4e, 0x55, 0x53, 0x42,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
            0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        ]
        usb_device_status_report = [
            0x00, 0x00,
        ]
        memory_contents = usb_device_descriptor + usb_config_descriptor \
                        + usb_string0_descriptor + usb_string_manufacturer \
                        + usb_string_product + usb_bos_descriptor \
                        + usb_ms_compat_id_descriptor + usb_device_status_report
        out_buffer = self.specials.out_buffer = Memory(8, len(memory_contents), init=memory_contents)
        descriptor_bytes_remaining = Signal(6) # Maximum number of bytes in USB is 64
        self.specials.out_buffer_rd = out_buffer_rd = out_buffer.get_port(write_capable=False, clock_domain="usb_12")

        # Indicates DATA1 or DATA0
        dtb_polarity = Signal()

        last_start = Signal()

        # Set to 1 if we have a response that matches the requested descriptor
        have_response = self.have_response = Signal()

        # Needs to be able to index Memory
        response_addr = Signal(9)
        response_len = Signal(7)
        response_ack = Signal()

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
        data_recv_put_delayed = self.data_recv_put_delayed = Signal()
        data_recv_payload_delayed = self.data_recv_payload_delayed = Signal(8)
        self.sync += [
            data_recv_put_delayed.eq(usb_core.data_recv_put),
            data_recv_payload_delayed.eq(usb_core.data_recv_payload),
        ]

        # Wire up debug signals if required
        if debug:
            debug_bridge = USBWishboneBridge(usb_core)
            self.submodules.debug_bridge = ClockDomainsRenamer("usb_12")(debug_bridge)
            self.comb += [
                debug_packet_detected.eq(~self.debug_bridge.n_debug_in_progress),
                debug_sink_data.eq(self.debug_bridge.sink_data),
                debug_sink_data_ready.eq(self.debug_bridge.sink_valid),
                debug_ack_response.eq(self.debug_bridge.send_ack | self.debug_bridge.sink_valid),
            ]

        self.comb += [
            # This needs to be correct *before* token is finished, everything
            # else uses registered outputs.
            usb_core.sta.eq((~(have_response | response_ack) & ~debug_packet_detected) & ~debug_sink_data_ready),
            usb_core.arm.eq(((have_response | response_ack) & ~debug_packet_detected) | debug_ack_response),
            usb_core.dtb.eq(dtb_polarity | debug_packet_detected),

            If(debug_packet_detected,
                debug_data_mux.eq(debug_sink_data),
                debug_data_ready_mux.eq(debug_sink_data_ready),
            ).Else(
                debug_data_mux.eq(out_buffer_rd.dat_r),
                debug_data_ready_mux.eq(response_len > 0),
            ),
            out_buffer_rd.adr.eq(response_addr),
            usb_core.data_send_have.eq(debug_data_ready_mux),
            usb_core.data_send_payload.eq(debug_data_mux),
            have_response.eq(response_len > 0),
        ]

        self.sync += [
            last_start.eq(usb_core.start),
            If(last_start,
                If(usb_core.tok == PID.SETUP,
                    setup_index.eq(0),
                    dtb_polarity.eq(1),
                    response_len.eq(0),
                )
            ),
            If(usb_core.tok == PID.SETUP,
                If(data_recv_put_delayed,
                    If(setup_index < 8,
                        setup_index.eq(setup_index + 1),
                    ),
                    Case(setup_index, {
                        0: bmRequestType.eq(data_recv_payload_delayed),
                        1: bRequest.eq(data_recv_payload_delayed),
                        2: wValue.eq(data_recv_payload_delayed),
                        3: wValue.eq(Cat(wValue[0:8], data_recv_payload_delayed)),
                        4: wIndex.eq(data_recv_payload_delayed),
                        5: wIndex.eq(Cat(wIndex[0:8], data_recv_payload_delayed)),
                        6: wLength.eq(data_recv_payload_delayed),
                        7: wLength.eq(Cat(wLength[0:8], data_recv_payload_delayed)),
                    }),
                ),
            ),
            If(usb_core.setup,
                If(bmRequestType == 0x80,
                    If(bRequest == 0x06,
                        If(wValue == 0x0100,
                            response_addr.eq(0),
                            If(wLength > len(usb_config_descriptor),
                                response_len.eq(len(usb_device_descriptor)),
                            ).Else(
                                response_len.eq(wLength),
                            ),
                        ).Elif(wValue == 0x0200,
                            response_addr.eq(len(usb_device_descriptor)),
                            If(wLength > len(usb_config_descriptor),
                                response_len.eq(len(usb_config_descriptor)),
                            ).Else(
                                response_len.eq(wLength),
                            ),
                        ).Elif(wValue == 0x0300,
                            response_addr.eq(len(usb_device_descriptor) + len(usb_config_descriptor)),
                            If(wLength > len(usb_string0_descriptor),
                                response_len.eq(len(usb_string0_descriptor)),
                            ).Else(
                                response_len.eq(wLength),
                            ),
                        ).Elif(wValue == 0x0301,
                            response_addr.eq(len(usb_device_descriptor) + len(usb_config_descriptor) + len(usb_string0_descriptor)),
                            If(wLength > len(usb_string_manufacturer),
                                response_len.eq(len(usb_string_manufacturer)),
                            ).Else(
                                response_len.eq(wLength),
                            ),
                        ).Elif(wValue == 0x0302,
                            response_addr.eq(len(usb_device_descriptor) + len(usb_config_descriptor) + len(usb_string0_descriptor) + len(usb_string_manufacturer)),
                            If(wLength > len(usb_string_product),
                                response_len.eq(len(usb_string_product)),
                            ).Else(
                                response_len.eq(wLength),
                            ),
                        ).Elif(wValue == 0x0f00,
                            response_addr.eq(len(usb_device_descriptor) + len(usb_config_descriptor) + len(usb_string0_descriptor) + len(usb_string_manufacturer) + len(usb_string_product)),
                            If(wLength > len(usb_bos_descriptor),
                                response_len.eq(len(usb_bos_descriptor)),
                            ).Else(
                                response_len.eq(wLength),
                            ),
                        ).Elif(wValue == 0x0f00,
                            response_ack.eq(1),
                        ),
                    ).Elif(bRequest == 0x00,
                        response_addr.eq(len(usb_device_descriptor) + len(usb_config_descriptor) + len(usb_string0_descriptor) + len(usb_string_manufacturer) + len(usb_string_product) + len(usb_bos_descriptor) + len(usb_ms_compat_id_descriptor)),
                        If(wLength > len(usb_device_status_report),
                            response_len.eq(len(usb_device_status_report)),
                        ).Else(
                            response_len.eq(wLength),
                        ),
                    ),
                # MS Extended Compat ID OS Feature
                ).Elif(bmRequestType == 0xc0,
                    response_addr.eq(len(usb_device_descriptor) + len(usb_config_descriptor) + len(usb_string0_descriptor) + len(usb_string_manufacturer) + len(usb_string_product) + len(usb_bos_descriptor)),
                    If(wLength > len(usb_ms_compat_id_descriptor),
                        response_len.eq(len(usb_ms_compat_id_descriptor)),
                    ).Else(
                        response_len.eq(wLength),
                    ),
                # Set Address / Configuration
                ).Elif(bmRequestType == 0x00,
                    response_ack.eq(1),
                ),
            ),
            If(usb_core.data_send_get,
                response_ack.eq(1),
                response_addr.eq(response_addr + 1),
                If(response_len,
                    response_len.eq(response_len - 1),
                ),
            ),
            If(self.data_recv_put_delayed,
                response_ack.eq(0),
            ),
        ]
