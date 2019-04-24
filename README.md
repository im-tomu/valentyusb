# VanetyUSB

USB Full-Speed core written in migen/LiteX.  This core has been tested and is known to work on various incarnations of Fomu.  It requires you to have a 48 MHz clock and a 12 MHz clock.

## Usage

To use this in your project, instantiate one of the CPU instances.  The `epfifo` instances is the most widely-tested API:

```python
        usb_pads = platform.request("usb")
        usb_iobuf = usbio.IoBuf(usb_pads.d_p, usb_pads.d_n, usb_pads.pullup)
        self.submodules.usb = epfifo.PerEndpointFifoInterface(usb_iobuf, debug=usb_debug)
        if usb_debug:
            self.add_wb_master(self.usb.debug_bridge.wishbone)
```

## Running Simulations

Simulations are the most important part of hardware development.  By presenting a predefined pattern to your module, you can find edgecases before they become a problem.  Strange off-by-one errors can be caught before you spend a lot of time synthesizing and loading onto target hardware.