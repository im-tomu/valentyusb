#!/usr/bin/env python3
# This variable defines all the external programs that this module
# relies on.  lxbuildenv reads this variable in order to ensure
# the build will finish without exiting due to missing third-party
# programs.
LX_DEPENDENCIES = []

# Import lxbuildenv to integrate the deps/ directory
#import lxbuildenv

# Disable pylint's E1101, which breaks completely on migen
#pylint:disable=E1101

#from migen import *
from migen import Module, Signal, Instance, ClockDomain, If
from migen.genlib.resetsync import AsyncResetSynchronizer
from migen.fhdl.specials import TSTriple
from migen.fhdl.bitcontainer import bits_for
from migen.fhdl.structure import ClockSignal, ResetSignal, Replicate, Cat

from litex.build.sim.platform import SimPlatform
from litex.build.generic_platform import Pins, IOStandard, Misc, Subsignal
from litex.soc.integration import SoCCore
from litex.soc.integration.builder import Builder
from litex.soc.integration.soc_core import csr_map_update
from litex.soc.interconnect import wishbone
from litex.soc.interconnect.csr import AutoCSR, CSRStatus, CSRStorage

from valentyusb import usbcore
from valentyusb.usbcore import io as usbio
from valentyusb.usbcore.cpu import dummyusb, eptri
from valentyusb.usbcore.endpoint import EndpointType

import argparse
import os

_io = [
    # Wishbone
    ("wishbone", 0,
        Subsignal("adr",   Pins(30)),
        Subsignal("dat_r", Pins(32)),
        Subsignal("dat_w", Pins(32)),
        Subsignal("sel",   Pins(4)),
        Subsignal("cyc",   Pins(1)),
        Subsignal("stb",   Pins(1)),
        Subsignal("ack",   Pins(1)),
        Subsignal("we",    Pins(1)),
        Subsignal("cti",   Pins(3)),
        Subsignal("bte",   Pins(2)),
        Subsignal("err",   Pins(1))
    ),
    ("usb", 0,
        Subsignal("d_p", Pins(1)),
        Subsignal("d_n", Pins(1)),
        Subsignal("pullup", Pins(1)),
    ),
    ("clk", 0,
        Subsignal("clk48", Pins(1)),
        Subsignal("clk12", Pins(1)),
    )
]

_connectors = []

class _CRG(Module):
    def __init__(self, platform):
        clk = platform.request("clk")
        clk12 = Signal()

        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_usb_12 = ClockDomain()
        self.clock_domains.cd_usb_48 = ClockDomain()

        # platform.add_period_constraint(self.cd_usb_48.clk, 1e9/48e6)
        # platform.add_period_constraint(self.cd_sys.clk, 1e9/12e6)
        # platform.add_period_constraint(self.cd_usb_12.clk, 1e9/12e6)
        # platform.add_period_constraint(clk48_raw, 1e9/48e6)

        clk48 = clk.clk48
        self.comb += clk.clk12.eq(clk12)

        self.comb += self.cd_usb_48.clk.eq(clk48)

        clk12_counter = Signal(2)
        self.sync.usb_48 += clk12_counter.eq(clk12_counter + 1)

        self.comb += clk12.eq(clk12_counter[1])

        self.comb += self.cd_sys.clk.eq(clk12)
        self.comb += self.cd_usb_12.clk.eq(clk12)

class Platform(SimPlatform):
    def __init__(self, toolchain="verilator"):
        SimPlatform.__init__(self, "sim", _io, _connectors, toolchain="verilator")

    def create_programmer(self):
        raise ValueError("programming is not supported")

class BaseSoC(SoCCore):
    SoCCore.csr_map = {
        "ctrl":           0,  # provided by default (optional)
        "crg":            1,  # user
        "uart_phy":       2,  # provided by default (optional)
        "uart":           3,  # provided by default (optional)
        "identifier_mem": 4,  # provided by default (optional)
        "timer0":         5,  # provided by default (optional)
        "cpu_or_bridge":  8,
        "usb":            9,
        "picorvspi":      10,
        "touch":          11,
        "reboot":         12,
        "rgb":            13,
        "version":        14,
    }

    SoCCore.mem_map = {
        "rom":      0x00000000,  # (default shadow @0x80000000)
        "sram":     0x10000000,  # (default shadow @0xa0000000)
        "spiflash": 0x20000000,  # (default shadow @0xa0000000)
        "main_ram": 0x40000000,  # (default shadow @0xc0000000)
        "csr":      0x60000000,  # (default shadow @0xe0000000)
    }

    interrupt_map = {
        "usb": 3,
    }
    interrupt_map.update(SoCCore.interrupt_map)

    def __init__(self, platform, output_dir="build", **kwargs):
        # Disable integrated RAM as we'll add it later
        self.integrated_sram_size = 0

        self.output_dir = output_dir

        clk_freq = int(12e6)
        self.submodules.crg = _CRG(platform)

        SoCCore.__init__(self, platform, clk_freq, 
            cpu_type=None,
            integrated_rom_size=0x0,
            integrated_sram_size=0x0,
            integrated_main_ram_size=0x0,
            csr_address_width=14, csr_data_width=8,
            with_uart=False, with_timer=False)

        # Add USB pads
        usb_pads = platform.request("usb")
        usb_iobuf = usbio.IoBuf(usb_pads.d_p, usb_pads.d_n, usb_pads.pullup)
        self.submodules.usb = eptri.TriEndpointInterface(usb_iobuf)

        class _WishboneBridge(Module):
            def __init__(self, interface):
                self.wishbone = interface

        self.add_cpu(_WishboneBridge(self.platform.request("wishbone")))
        self.add_wb_master(self.cpu.wishbone)

def main():
    parser = argparse.ArgumentParser(
        description="Build Fomu Main Gateware")
    args = parser.parse_args()

    output_dir = 'build'

    platform = Platform()
    soc = BaseSoC(platform, cpu_type=None, cpu_variant=None,
                            output_dir=output_dir)
    builder = Builder(soc, output_dir=output_dir, csr_csv="csr.csv", compile_software=False)
    vns = builder.build(run=False)
    soc.do_exit(vns)

    print(
"""Simulation build complete.  Output files:
    {}/gateware/dut.v               Source Verilog file.  Run this under Cocotb.
""".format(output_dir))

if __name__ == "__main__":
    main()
