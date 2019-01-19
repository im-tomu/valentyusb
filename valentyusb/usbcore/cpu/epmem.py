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


class MemInterface(Module, AutoCSR):
    """
    Interfaces the USB state machine core to the soft CPU.

    This interface has two memory regions;
     * Output memory. Writable by CPU, readable by USB Core.
     * Input memory. Writable by USB Core, readable by CPU.

    Each endpoint has;
     * A current pointer
     * A current length
     * Control bits
     * A pending flag

    Pointers are all relative to the start of the memory.

    On output endpoints, both the pointer and length are read only.
    On input endpoints, the pointer and length are writable.

    To accept / send data from an endpoint you set the arm bit. The USB core
    will then respond to the next request and update the pointer / length.

    After a packet has been sent or received, the pending flag will be raised.
    While the pending flag is raised, the USB core will respond with NAK.
    """

    def csr_bits(self, csr):
        l = value_bits_sign(csr.storage)[0]
        bits = [Signal() for i in range(l)]
        self.comb += [bits[i].eq(csr.storage[i]) for i in range(l)]
        return Array(bits)

    def __init__(self, iobuf, num_endpoints=3, depth=512):

        ptr_width = 9 # Signal(max=depth).size

        self.submodules.usb_core = usb_core = UsbTransfer(iobuf)

        self.submodules.pullup = GPIOOut(usb_core.iobuf.usb_pullup)
        self.iobuf = usb_core.iobuf

        #self.submodules.packet = ev.EventManager()
        #self.packet.setup = ev.EventSourcePulse()
        #self.submodules.setup_ptr = CSRStatus(ptr_width)

        # Output endpoints
        all_trig = []
        trig = []

        self.submodules.packet = ev.EventManager()
        for i in range(0, num_endpoints):
            exec("self.packet.oep{} = ev.EventSourcePulse()".format(i))
            t = getattr(self.packet, "oep{}".format(i)).trigger
            all_trig.append(t.eq(1))
            trig.append(t)

            exec("self.packet.iep{} = ev.EventSourcePulse()".format(i))
            t = getattr(self.packet, "iep{}".format(i)).trigger
            all_trig.append(t.eq(1))
            trig.append(t)

        self.packet.finalize()

        self.eps_idx = eps_idx = Signal(5)
        self.comb += [
            self.eps_idx.eq(Cat(usb_core.endp, usb_core.tok == PID.IN)),
        ]

        l = num_endpoints * 2

        self.submodules.sta = CSRStorage(l)                         # Stall endpoint
        self.submodules.dtb = CSRStorage(l, write_from_dev=True)    # Data toggle bit
        self.submodules.arm = CSRStorage(l)                         # Endpoint is ready

        self.comb += [
            usb_core.sta.eq(self.csr_bits(self.sta)[eps_idx]),
            usb_core.dtb.eq(self.csr_bits(self.dtb)[eps_idx]),
            usb_core.arm.eq(self.csr_bits(self.arm)[eps_idx]), # & Array(self.packet.pending.r)[eps_idx]),
            If(~iobuf.usb_pullup,
                *all_trig,
            ).Else(
                Array(trig)[usb_core.ep_addr].eq(usb_core.commit),
            ),
        ]

        # Output pathway
        # -----------------------
        self.specials.obuf = Memory(8, depth)
        self.specials.oport_wr = self.obuf.get_port(write_capable=True, clock_domain="usb_12")
        self.specials.oport_rd = self.obuf.get_port(clock_domain="sys")

        optrs = []
        for i in range(0, num_endpoints):
            exec("self.submodules.optr_ep{0} = CSRStatus(ptr_width, name='optr_ep{0}')".format(i))
            optrs.append(getattr(self, "optr_ep{}".format(i)).status)

        self.obuf_ptr = Signal(ptr_width)
        self.comb += [
            self.oport_wr.adr.eq(self.obuf_ptr),
            self.oport_wr.dat_w.eq(usb_core.data_recv_payload),
            self.oport_wr.we.eq(usb_core.data_recv_put),
        ]
        # On a commit, copy the current obuf_ptr to the CSR register.
        self.sync += [
            If(usb_core.commit,
                If((usb_core.tok == PID.OUT) | (usb_core.tok == PID.SETUP),
                    Array(optrs)[usb_core.endp].eq(self.obuf_ptr),
                ),
            ),
        ]
        self.sync.usb_12 += [
            If(usb_core.data_recv_put, self.obuf_ptr.eq(self.obuf_ptr + 1)),
        ]

        # Input pathway
        # -----------------------
        self.specials.ibuf = Memory(8, depth)
        self.specials.iport_wr = self.ibuf.get_port(write_capable=True, clock_domain="sys")
        self.specials.iport_rd = self.ibuf.get_port(clock_domain="usb_12")

        #for i in range(0, num_endpoints):
        #    exec("self.submodules.iptr_ep{0} = CSRStorage(ptr_width, name='iptr_ep{0}')".format(i))
        #    iptrs.append(getattr(self, "iptr_ep{}".format(i)).storage)
        #
        #    exec("self.submodules.ilen_ep{0} = CSRStorage(ptr_width, name='ilen_ep{0}')".format(i))
        #    ilens.append(getattr(self, "ilen_ep{}".format(i)).storage)
        assert num_endpoints == 3
        self.submodules.iptr_ep0 = CSRStorage(ptr_width)
        self.submodules.ilen_ep0 = CSRStorage(ptr_width)
        self.submodules.iptr_ep1 = CSRStorage(ptr_width)
        self.submodules.ilen_ep1 = CSRStorage(ptr_width)
        self.submodules.iptr_ep2 = CSRStorage(ptr_width)
        self.submodules.ilen_ep2 = CSRStorage(ptr_width)
        iptrs = [self.iptr_ep0.storage,self.iptr_ep1.storage,self.iptr_ep2.storage]
        ilens = [self.ilen_ep0.storage,self.ilen_ep1.storage,self.ilen_ep2.storage]

        self.ibuf_ptr = Signal(ptr_width)
        self.comb += [
            self.iport_rd.adr.eq(self.ibuf_ptr),
            usb_core.data_send_payload.eq(self.iport_rd.dat_r),
            #self.iport_rd.re.eq(),
        ]
        # On a transfer start, copy the CSR register into ibuf_ptr
        self.sync += [
            If(usb_core.start,
                self.ibuf_ptr.eq(Array(iptrs)[usb_core.endp]),
            ),
        ]
        self.sync.usb_12 += [
            If(usb_core.data_send_get, self.ibuf_ptr.eq(self.ibuf_ptr + 1)),
        ]
        self.comb += [
            usb_core.data_send_have.eq(self.ibuf_ptr != Array(ilens)[usb_core.endp]),
        ]
