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

from ..pid import PID, PIDTypes
from ..sm.transfer import UsbTransfer


class MemInterface(Module, AutoCSR):
    """
    Interfaces the USB state machine core to the soft CPU.

    This interface has two memory regions:
     * Output memory. Writable by CPU, readable by USB Core.
     * Input memory. Writable by USB Core, readable by CPU.

    Each endpoint has:
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

    The `arm`, `dtb`, and `sta` registers are bitmasks.  They are packed
    in pairs of IO.  If you only have one endpoint, then `arm`, `dtb`, and
    `sta` are packed like this:
        IO
    Where
        Bit 1 is set to affect EP0 IN, and
        Bit 0 is set to affect EP0 OUT
    Likewise, if you have 3 endpoints, they are packed as:
        IOIOIO
        |||||\- EP0 OUT
        ||||\-- EP0 IN
        |||\--- EP1 OUT
        ||\---- EP1 IN
        |\----- EP2 OUT
        \------ EP2 IN

    Therefore, to ARM the EP1 IN endpoint, do:
        arm_write((1<<1) | 1);
    Or for EP2 IN:
        arm_write((1<<2) | 1);
    """

    def csr_bits(self, csr):
        """
        Work around the lack of bit-addressability in CSRs by creating
        an array of signals that are aliases of the various CSR storage
        values.
        """
        l = value_bits_sign(csr.storage)[0]
        bits = [Signal() for i in range(l)]
        self.comb += [bits[i].eq(csr.storage[i]) for i in range(l)]
        return Array(bits)

    def __init__(self, iobuf, num_endpoints=3, depth=512):

        ptr_width = 9 # Signal(max=depth).size

        self.submodules.usb_core = usb_core = UsbTransfer(iobuf)

        self.submodules.pullup = GPIOOut(usb_core.iobuf.usb_pullup)
        self.iobuf = usb_core.iobuf

        # Output endpoints
        all_trig = []
        trig = []

        self.submodules.ev = ev.EventManager()
        for i in range(0, num_endpoints):
            exec("self.ev.oep{} = ev.EventSourcePulse()".format(i))
            t = getattr(self.ev, "oep{}".format(i)).trigger
            all_trig.append(t.eq(1))
            trig.append(t)

            exec("self.ev.iep{} = ev.EventSourcePulse()".format(i))
            t = getattr(self.ev, "iep{}".format(i)).trigger
            all_trig.append(t.eq(1))
            trig.append(t)

        self.ev.finalize()

        # eps_idx is the result of the last IN/OUT/SETUP token, and
        # therefore describes the current EP that the USB core sees.
        # The register is of the form:
        #    EEEEI
        # Where:
        #   E: The last endpoint number
        #   I: True if the current endpoint is an IN endpoint
        self.eps_idx = eps_idx = Signal(5)

        self.comb += [
            # Sic. The Cat() function places the first argument in the LSB,
            # and the second argument in the MSB.
            self.eps_idx.eq(Cat(usb_core.tok == PID.IN, usb_core.endp)),

            # self.eps_idx_in.eq(Cat(0, usb_core.endp)),
        ]

        signal_bits = num_endpoints * 2

        # Keep a copy of the control bits for each endpoint

        # Stall endpoint
        self.sta = CSRStorage(signal_bits, write_from_dev=True)

        # Data toggle bit
        self.dtb = CSRStorage(signal_bits, write_from_dev=True)

        # Endpoint is ready
        self.arm = CSRStorage(signal_bits, write_from_dev=True)

        # Wire up the USB core control bits to the currently-active
        # endpoint bit.
        self.comb += [
            usb_core.sta.eq(self.csr_bits(self.sta)[eps_idx]),
            usb_core.arm.eq(self.csr_bits(self.arm)[eps_idx]),
            usb_core.dtb.eq(~self.csr_bits(self.dtb)[eps_idx]),
            If(~iobuf.usb_pullup,
                *all_trig,
            ).Else(
                Array(trig)[eps_idx].eq(usb_core.commit),
            ),
        ]

        # Output pathway
        # -----------------------
        self.specials.obuf = Memory(8, depth)
        self.specials.oport_wr = self.obuf.get_port(write_capable=True, clock_domain="usb_12")
        self.specials.oport_rd = self.obuf.get_port(clock_domain="sys")

        optrs = []
        for i in range(0, num_endpoints):
            exec("self.optr_ep{0} = CSRStatus(ptr_width, name='optr_ep{0}')".format(i))
            optrs.append(getattr(self, "optr_ep{}".format(i)).status)

        self.obuf_ptr = Signal(ptr_width)
        self.comb += [
            self.oport_wr.adr.eq(self.obuf_ptr),
            self.oport_wr.dat_w.eq(usb_core.data_recv_payload),
            self.oport_wr.we.eq(usb_core.data_recv_put),
        ]
        # On a commit, copy the current obuf_ptr to the CSR register.
        self.sync.usb_12 += [
            If(usb_core.commit,
                If((usb_core.tok == PID.OUT) | (usb_core.tok == PID.SETUP),
                    Array(optrs)[usb_core.endp].eq(self.obuf_ptr),
                ),
            ),
        ]

        # Set up a signal to reset EP0 when we get a SETUP packet
        self.usb_ep0_reset = Signal()
        self.update_dtb = Signal()
        self.update_ctrl = Signal()
        self.should_check_ep0 = Signal()
        #self.obuf_full = Signal()
        #self.ibuf_empty = Signal()

        # self.comb += [
        #     self.usb_ep0_reset.eq(usb_core.start & (usb_core.tok == PID.SETUP))
        # ]

        self.sync.usb_12 += [
            self.arm.we.eq(0),
            self.sta.we.eq(0),
            self.dtb.we.eq(0),
            If(usb_core.data_recv_put,
                self.obuf_ptr.eq(self.obuf_ptr + 1)
            ),

            # If the EP0 needs resetting, then clear the EP0 IN and OUT bits, which
            # are stored in the lower two bits of the three control registers.
            If(usb_core.start,
                self.should_check_ep0.eq(1),
            ).Elif(self.should_check_ep0,
                self.should_check_ep0.eq(0),
                If(usb_core.tok == PID.SETUP,
                    self.update_ctrl.eq(1),
                    self.update_dtb.eq(1),
                    self.arm.dat_w.eq(self.arm.storage & ~0b11),
                    self.sta.dat_w.eq(self.sta.storage & ~0b11),
                    self.dtb.dat_w.eq(self.dtb.storage & ~0b11),
                ),
            ).Elif(usb_core.commit,
                #self.update_ctrl.eq((self.obuf_full & ~eps_idx[0]) | (self.ibuf_empty & eps_idx[0])),
                self.update_ctrl.eq(1),
                self.update_dtb.eq(1),
                self.arm.dat_w.eq((self.arm.storage & ~(1 << eps_idx))),
                self.sta.dat_w.eq(self.sta.storage),
                self.dtb.dat_w.eq((self.dtb.storage ^ (1 << eps_idx))),
            ),
            If(self.update_ctrl,
                self.update_ctrl.eq(0),
                self.arm.we.eq(1),
                self.sta.we.eq(1),
            ),
            If(self.update_dtb,
                self.update_dtb.eq(0),
                self.dtb.we.eq(1),
            )
        ]

        #self.diff_addr = Signal(ptr_width)
        #self.comb += [
        #    self.diff_addr.eq(self.oport_rd.adr - self.oport_wr.adr),
        #    # check if there are at least 64 Bytes (== FS max packet size) left in the out buffer
        #    self.obuf_full.eq((self.diff_addr != 0) & (self.diff_addr <= 64)),
        #    #self.obuf_full.eq(  (self.diff_addr[7::] == 0) &
        #    #                    (self.diff_addr[0:7] != 0)),
        #]

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
        self.iptr_ep0 = CSRStorage(ptr_width)
        self.ilen_ep0 = CSRStorage(ptr_width)
        self.iptr_ep1 = CSRStorage(ptr_width)
        self.ilen_ep1 = CSRStorage(ptr_width)
        self.iptr_ep2 = CSRStorage(ptr_width)
        self.ilen_ep2 = CSRStorage(ptr_width)
        iptrs = [self.iptr_ep0.storage,self.iptr_ep1.storage,self.iptr_ep2.storage]
        ilens = [self.ilen_ep0.storage,self.ilen_ep1.storage,self.ilen_ep2.storage]

        self.ibuf_ptr = Signal(ptr_width)
        self.comb += [
            self.iport_rd.adr.eq(self.ibuf_ptr),
            usb_core.data_send_payload.eq(self.iport_rd.dat_r),
            #self.iport_rd.re.eq(),
        ]
        # On a transfer start, copy the CSR register into ibuf_ptr
        self.sync.usb_12 += [
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
