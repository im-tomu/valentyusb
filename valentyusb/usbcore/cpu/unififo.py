#!/usr/bin/env python3

from migen import *
from migen.genlib import fifo
from migen.genlib import cdc

from litex.soc.interconnect import csr_eventmanager as ev
from litex.soc.interconnect.csr import *
from litex.soc.cores.gpio import GPIOOut

from ..endpoint import *
from ..io import FakeIoBuf
from ..rx.pipeline import RxPipeline
from ..tx.pipeline import TxPipeline

from ..utils.packet import *


class UsbUniFifo(Module, AutoCSR):
    """
    Presents the USB data stream as two FIFOs via CSR registers.
    """

    def __init__(self, iobuf):
        self.submodules.ev = ev.EventManager()
        self.ev.submodules.rx = ev.EventSourcePulse()

        # ---------------------
        # RX side
        # ---------------------
        self.submodules.rx = rx = RxPipeline()
        self.byte_count = CSRStatus(8)

        obuf = fifo.AsyncFIFOBuffered(width=8, depth=128)
        self.submodules.obuf = ClockDomainsRenamer({"write": "usb_12", "read": "sys"})(obuf)

        # USB side (writing)
        self.comb += [
            self.obuf.din.eq(self.rx.o_data_payload),
            self.obuf.we.eq(self.rx.o_data_strobe),
        ]
        self.sync.usb_12 += [
            self.ev.rx.trigger.eq(self.rx.o_pkt_end),
            If(self.rx.o_data_strobe, self.byte_count.status.eq(self.byte_count.status + 1))
        ]

        # System side (reading)
        self.obuf_head = CSR(8)
        self.obuf_empty = CSRStatus(1)
        self.comb += [
            self.obuf_head.w.eq(self.obuf.dout),
            self.obuf.re.eq(self.obuf_head.re),
            self.obuf_empty.status.eq(~self.obuf.readable),
        ]

        # ---------------------
        # TX side
        # ---------------------
        self.submodules.tx = tx = TxPipeline()

        ibuf = fifo.AsyncFIFOBuffered(width=8, depth=128)
        self.submodules.ibuf = ClockDomainsRenamer({"write": "sys", "read": "usb_12"})(ibuf)

        # System side (writing)
        self.arm = CSRStorage(1)
        self.ibuf_head = CSR(8)
        self.ibuf_empty = CSRStatus(1)
        self.comb += [
            self.ibuf.din.eq(self.ibuf_head.r),
            self.ibuf.we.eq(self.ibuf_head.re),
            self.ibuf_empty.status.eq(~self.ibuf.readable & ~tx.o_oe),
        ]

        # USB side (reading)
        self.comb += [
            tx.i_data_payload.eq(self.ibuf.dout),
            self.ibuf.re.eq(tx.o_data_strobe & self.arm.storage),
        ]
        self.sync.usb_12 += [
            tx.i_oe.eq(self.ibuf.readable & self.arm.storage),
        ]

        # ----------------------
        # USB 48MHz bit strobe
        # ----------------------
        self.comb += [
            tx.i_bit_strobe.eq(rx.o_bit_strobe),
        ]

        # ----------------------
        # Tristate
        # ----------------------
        self.submodules.iobuf = iobuf
        self.comb += [
            rx.i_usbp.eq(iobuf.usb_p_rx),
            rx.i_usbn.eq(iobuf.usb_n_rx),
            iobuf.usb_tx_en.eq(tx.o_oe),
            iobuf.usb_p_tx.eq(tx.o_usbp),
            iobuf.usb_n_tx.eq(tx.o_usbn),
        ]
        self.submodules.pullup = GPIOOut(iobuf.usb_pullup)
