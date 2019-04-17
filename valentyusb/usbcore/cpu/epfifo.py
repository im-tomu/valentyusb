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


class FakeFifo(Module):
    def __init__(self):
        self.din = Signal(8)
        self.writable = Signal(1)
        self.we = Signal(1)

        self.dout = Signal(8)
        self.readable = Signal(1)
        self.re = Signal(1)


class Endpoint(Module, AutoCSR):
    def __init__(self):
        self.submodules.ev = ev.EventManager()
        self.ev.submodules.error = ev.EventSourcePulse()
        self.ev.submodules.packet = ev.EventSourcePulse()
        self.ev.finalize()

        self.trigger = self.ev.packet.trigger

        # Last PID?
        self.last_tok = CSRStatus(2)

        # How to respond to requests;
        #  - 10 - No response
        #  - 00 - ACK
        #  - 01 - NAK
        #  - 11 - STALL
        self.respond = CSRStorage(2, write_from_dev=True)

        self.response = Signal(2)
        self.reset = Signal()
        self.comb += [
            self.response.eq(Cat(
                    self.respond.storage[0] | self.ev.packet.pending,
                    self.respond.storage[1],
            )),
        ]
        self.comb += [
            self.respond.dat_w.eq(EndpointResponse.NAK),
            self.respond.we.eq(self.reset),
        ]

        self.dtb = CSRStorage(1, write_from_dev=True)
        self.comb += [
            self.dtb.dat_w.eq(~self.dtb.storage | self.reset),
        ]
        # When triggered, flip the data toggle bit
        toggle = Signal()
        self.sync += [
            If(self.trigger | self.reset,
                If(~toggle,
                    toggle.eq(1),
                    self.dtb.we.eq(1),
                ).Else(
                    self.dtb.we.eq(0),
                ),
            ).Else(
                self.dtb.we.eq(0),
                toggle.eq(0),
            ),
        ]

        self.submodules.fake = FakeFifo()
        self.ibuf = None
        self.obuf = None


class EndpointNone(Module):
    def __init__(self):
        self.ibuf = FakeFifo()
        self.obuf = FakeFifo()
        self.response = Signal(reset=EndpointResponse.NAK)
        self.trigger = Signal()
        self.reset = Signal()

        self.last_tok = Module()
        self.last_tok.status = Signal(2)

        self.dtb = Module()
        self.dtb.storage = Signal()


class EndpointOut(Endpoint):
    """Endpoint for Host->Device data.

    Raises packet IRQ when new packet has arrived.
    CPU reads from the head CSR to get front data from FIFO.
    CPU writes to head CSR to advance the FIFO by one.
    """
    def __init__(self):
        Endpoint.__init__(self)

        self.submodules.obuf = ClockDomainsRenamer({"write": "usb_12", "read": "sys"})(
            fifo.AsyncFIFOBuffered(width=8, depth=128))

        self.obuf_head = CSR(8)
        self.obuf_empty = CSRStatus(1)
        self.comb += [
            self.obuf_head.w.eq(self.obuf.dout),
            self.obuf.re.eq(self.obuf_head.re),
            self.obuf_empty.status[0].eq(~self.obuf.readable),
        ]
        self.ibuf = self.fake


class EndpointIn(Endpoint):
    """Endpoint for Device->Host data.

    Reads from the buffer memory.
    Raises packet IRQ when packet has been sent.
    CPU writes to the head CSR to push data onto the FIFO.
    """
    def __init__(self):
        Endpoint.__init__(self)

        self.submodules.ibuf = ClockDomainsRenamer({"write": "sys", "read": "usb_12"})(
            fifo.AsyncFIFOBuffered(width=8, depth=512))

        xxxx_readable = Signal()
        self.specials.crc_readable = cdc.MultiReg(self.ibuf.readable, xxxx_readable)

        self.ibuf_head = CSR(8)
        self.ibuf_empty = CSRStatus(1)
        self.comb += [
            self.ibuf.din.eq(self.ibuf_head.r),
            self.ibuf.we.eq(self.ibuf_head.re),
            self.ibuf_empty.status[0].eq(~xxxx_readable),
        ]
        self.obuf = self.fake


class PerEndpointFifoInterface(Module, AutoCSR):
    """

    Implements a CPU interface with each endpoint having it's own FIFO.

    Each endpoint has;
     * A FIFO with one end connected to CSRs and the other to the USB core.
     * Control bits.
     * A pending flag.

    An output FIFO is written to using CSR registers.
    An input FIFO is read using CSR registers.

    Extra CSR registers set the response type (ACK/NAK/STALL).
    """

    def __init__(self, iobuf, endpoints=[EndpointType.BIDIR, EndpointType.IN, EndpointType.BIDIR], debug=False):
        size = 9

        # USB Core
        self.submodules.usb_core = usb_core = UsbTransfer(iobuf)

        self.submodules.pullup = GPIOOut(usb_core.iobuf.usb_pullup)
        self.iobuf = usb_core.iobuf

        # Endpoint controls
        ems = []
        eps = []
        trigger_all = []
        for i, endp in enumerate(endpoints):
            if endp & EndpointType.OUT:
                exec("self.submodules.ep_%s_out = ep = EndpointOut()" % i)
                oep = getattr(self, "ep_%s_out" % i)
                ems.append(oep.ev)
            else:
                oep = EndpointNone()

            trigger_all.append(oep.trigger.eq(1)),
            eps.append(oep)

            if endp & EndpointType.IN:
                exec("self.submodules.ep_%s_in = ep = EndpointIn()" % i)
                iep = getattr(self, "ep_%s_in" % i)
                ems.append(iep.ev)
            else:
                iep = EndpointNone()

            trigger_all.append(iep.trigger.eq(1)),
            eps.append(iep)

        self.submodules.ev = ev.SharedIRQ(*ems)

        self.eps = eps = Array(eps)
        self.eps_idx = eps_idx = Signal(5)
        self.comb += [
            self.eps_idx.eq(Cat(usb_core.tok == PID.IN, usb_core.endp)),
        ]

        ep0out_addr = EndpointType.epaddr(0, EndpointType.OUT)
        ep0in_addr = EndpointType.epaddr(0, EndpointType.IN)

        # Setup packet causes ep0 in and ep0 out to reset
        self.comb += [
            eps[ep0out_addr].reset.eq(usb_core.setup),
            eps[ep0in_addr].reset.eq(usb_core.setup),
        ]

        self.comb += [
            # This needs to be correct *before* token is finished, everything
            # else uses registered outputs.
            usb_core.sta.eq(eps[eps_idx].response == EndpointResponse.STALL),
            usb_core.arm.eq(eps[eps_idx].response == EndpointResponse.ACK),
            usb_core.dtb.eq(eps[eps_idx].dtb.storage),

            # Control signals
            If(~iobuf.usb_pullup,
                *trigger_all,
            ).Else(
                eps[eps_idx].trigger.eq(usb_core.commit),
            ),
            # FIFO
            # Host->Device[Out Endpoint] pathway
            eps[eps_idx].obuf.we.eq(usb_core.data_recv_put),
            eps[eps_idx].obuf.din.eq(usb_core.data_recv_payload),
            # [In Endpoint]Device->Host pathway
            usb_core.data_send_have.eq(eps[eps_idx].ibuf.readable),
            usb_core.data_send_payload.eq(eps[eps_idx].ibuf.dout),
            eps[eps_idx].ibuf.re.eq(usb_core.data_send_get),
        ]

        self.sync += [
            If(usb_core.commit,
                eps[eps_idx].last_tok.status.eq(usb_core.tok[2:]),
            ),
        ]
