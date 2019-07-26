# ValentyUSB

USB Full-Speed core written in Migen/LiteX.  This core has been tested and is known to work on various incarnations of Fomu.  It requires you to have a 48 MHz clock and a 12 MHz clock.  It optionally comes with a debug bridge for debugging Wishbone.

## Usage

To use this in your project, instantiate one of the CPU instances.  The `epfifo` instances is the most widely-tested API, however `dummyusb` can be used for designs without a CPU.

* **DummyUsb**: A wishbone device with no CPU interface that simply enumerates and can act as a Wishbone bridge
* **PerEndpointFifoInterface**: Requires a CPU to configure and manage the device.

```python
_io = [
    ("usb", 0,
        Subsignal("d_p", Pins("34")),
        Subsignal("d_n", Pins("37")),
        Subsignal("pullup", Pins("35")),
        IOStandard("LVCMOS33")
    ),
]

class BaseSoC(SoCCore):
    def __init__(self, platform, **kwargs):
        clk_freq = int(12e6)
        SoCCore.__init__(self, platform, clk_freq, with_uart=False, **kwargs)

        from valentyusb.usbcore.cpu import epfifo, dummyusb
        usb_pads = platform.request("usb")
        usb_iobuf = usbio.IoBuf(usb_pads.d_p, usb_pads.d_n, usb_pads.pullup)

        # If a CPU is present, add a per-endpoint interface.  Otherwise, add a dummy
        # interface that simply acts as a Wishbone bridge.
        # Note that the dummy interface only really makes sense when doing a debug build.
        # Also note that you can add a dummyusb interface to a CPU if you only care
        # about the wishbone bridge.
        if hasattr(self, "cpu"):
            self.submodules.usb = epfifo.PerEndpointFifoInterface(usb_iobuf, debug=usb_debug)
        else:
            self.submodules.usb = dummyusb.DummyUsb(usb_iobuf, debug=usb_debug)
        if usb_debug:
            self.add_wb_master(self.usb.debug_bridge.wishbone)
            self.register_mem("vexriscv_debug", 0xf00f0000, self.cpu.debug_bus, 0x100)

platform = LatticePlatform("ice40-up5k-sg48", _io, [], toolchain="icestorm")
soc = BaseSoC(platform, cpu_type="vexriscv", cpu_variant="min+debug") # set cpu_type=None to build without a CPU
builder = Builder(soc)
soc.do_exit(builder.build())
```

## Debug tools

You can use the `litex_server` built into the litex distribution to communicate with the device:

```sh
$ litex_server --usb --usb-pid 0x70bl
```

Alternately, you can use [wishbone-tool](https://github.com/xobs/wishbone-utils/releases):

```sh
$ wishbone-tool 0
INFO [wishbone_tool::usb_bridge] opened USB device device 017 on bus 001
Value at 00000000: 6f80106f
$
```

## GDB server

You can use `wishbone-tool` to run a GDB server:

```sh
$ wishbone-tool -s gdb
INFO [wishbone_tool::usb_bridge] opened USB device device 017 on bus 001
INFO [wishbone_tool] accepting connections on 0.0.0.0:1234
```