# ValentyUSB Simulation

Simulation is incredibly important for validating hardware designs.  This directory contains a [Cocotb](https://github.com/cocotb/cocotb) testbench for testing ValentyUSB.

It also contains some support code to make it easier to view the resulting output.

## Running the Simulation

You must ensure the following are all true:

* litex and migen are checked out alongside this repository -- that is, `litex`, `migen`, `litedram`, and `valentyusb` are all in the same directory
* You have `make` installed
* You have installed cocotb by running either `pip install cocotb` or `pip3 install cocotb`
* Icarus Verilog is installed, and you can run both `iverilog` and `vvp` (unless you're using a different simulator)
* A C compiler is installed, since iverilog requires it
* If you want to view vcd waveform diagrams, gtkwave is installed
* If you want to decode USB signals, `sigrok-cli` is installed

To run the simulation, ensure `iverilog` and `vvp` are in your PATH.  You should also ensure cocotb is installed:

```sh
$ pip install cocotb
```

You might need to also add `~/.local/bin` to your path, in order to have access to the `cocotb-config` script.

If you want to use `python3` instead of `python`, specify PYTHON_BIN as an argument to make, either by setting it as an environment variable or specifying it on the command line:

```sh
$ make PYTHON_BIN=python3
```

## Viewing the output

Cocotb will run the tests through the simulator.  As part of the testbench, a file called `dump.vcd` is created.  This contains all the signals from the simulation.  You can view this using `gtkwave`.

If you have `sigrok-cli` in your PATH, you can go one step further and run these signals through a logic analyzer.  A script has been created that will set this up for you:

```sh
$ gtkwave -S gtkwave.init dump.vcd
```

In order to get additional levels of decode, you can right-click on the area with signals and say `Add empty row`, and then drag this above the `usb_d_n` signal.  This can be repeated up to four times.

## About test names

Cocotb does not stop the simulator during the course of the run.  In order to identify various sections of the simulation, you need to add the `test_name` signal and convert it to `Ascii`.  The `gtkwave.init` script does this for you.

## FSM state names

Migen has Finite State Machine support.  The simulation engine adds additional signals to indicate which state the FSM is currently in.  These states have signals whose names end in `_state_name`.  You can add these signals to the decode output, right-click on them, select `Data Format` -> `Ascii` to get decoded state names.