#!/usr/bin/env python3

import unittest

from migen import *

from migen.fhdl.decorators import CEInserter, ResetInserter

from ..utils.packet import b
from ..test.common import BaseUsbTestCase


@ResetInserter()
@CEInserter()
class TxShifter(Module):
    """Transmit Shifter

    TxShifter accepts parallel data and shifts it out serially.

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Width of the data to be shifted.

    Input Ports
    -----------
    Input ports are passed in via the constructor.

    i_data : Signal(width)
        Data to be transmitted.

    Output Ports
    ------------
    Output ports are data members of the module. All outputs are flopped.

    o_data : Signal(1)
        Serial data output.

    o_get : Signal(1)
        Asserted the cycle after the shifter loads in i_data.

    """
    def __init__(self, width):
        self.i_data = Signal(width)
        self.o_get = Signal(1)
        self.o_empty = Signal(1)

        self.o_data = Signal(1)

        shifter = Signal(width)
        pos = Signal(width, reset=0b1)

        empty = Signal(1)
        self.sync += [
            self.o_data.eq(shifter[0]),
            pos.eq(pos >> 1),
            shifter.eq(shifter >> 1),
            If(empty,
                shifter.eq(self.i_data),
                pos.eq(1 << (width-1)),
            ),
            self.o_get.eq(empty),
        ]
        self.comb += [
            empty.eq(pos[0]),
            self.o_empty.eq(empty),
        ]


class TestTxShifter(BaseUsbTestCase):
    def test_shifter(self):
        test_vectors = {
            "basic shift out 1": dict(
                width = 8,
                data  = [b("00000001"), b("00000001"), b("00000001"), 0],
                reset = "-|________|________|________",
                ce    = "-|--------|--------|--------",
                out   = "0|00000001|00000001|00000001",
                get   = "-|_______-|_______-|_______-",
            ),
            "basic shift out 2": dict(
                width = 8,
                data  = [b("10000000"), b("10000000"), b("10000000"), 0],
                reset = "-|________|________|________",
                ce    = "-|--------|--------|--------",
                out   = "0|10000000|10000000|10000000",
                get   = "-|_______-|_______-|_______-",
            ),
            "basic shift out 3": dict(
                width = 8,
                data  = [b("01100110"), b("10000001"), b("10000000"), 0],
                reset = "-|________|________|________",
                ce    = "-|--------|--------|--------",
                out   = "0|01100110|10000001|10000000",
                get   = "-|_______-|_______-|_______-",
            ),
            "stall shift out 1": dict(
                width = 8,
                data  = [b("00000001"), b("00000001"), b("00000001"), 0],
                reset = "-|_________|________|________",
                ce    = "-|--_------|--------|--------",
                out   = "0|000000001|00000001|00000001",
                get   = "-|________-|_______-|_______-",
            ),
            "stall shift out 2": dict(
                width = 8,
                data  = [b("10000000"), b("10000000"), b("10000000"), 0],
                reset = "-|_________|________|________",
                ce    = "-|---_-----|--------|--------",
                out   = "0|100000000|10000000|10000000",
                get   = "-|________-|_______-|_______-",
            ),
            "stall shift out 3": dict(
                width = 8,
                data  = [b("01100110"), b("10000001"), b("10000000"), 0],
                reset = "-|_________|________|________",
                ce    = "-|---_-----|--------|--------",
                out   = "0|011100110|10000001|10000000",
                get   = "-|________-|_______-|_______-",
            ),
            "mutlistall shift out 1": dict(
                width = 8,
                data  = [b("00000001"), b("00000001"), b("00000001"), 0],
                reset = "-|___________|_________|_________",
                ce    = "-|--___------|--------_|----_----",
                out   = "0|00000000001|000000011|000000001",
                get   = "-|__________-|_______--|________-",
            ),
            "mutlistall shift out 2": dict(
                width = 8,
                data  = [b("10000000"), b("10000000"), b("10000000"), 0],
                reset = "-|____________|________|__________",
                ce    = "-|---____-----|--------|-_----_---",
                out   = "0|100000000000|10000000|1100000000",
                get   = "-|___________-|_______-|_________-",
            ),
            "mutlistall shift out 3": dict(
                width = 8,
                data  = [b("01100110"), b("10000001"), b("10000000"), 0],
                reset = "-|____________|___________|_________",
                ce    = "-|---____-----|--------___|-_-------",
                out   = "0|011111100110|10000001111|110000000",
                get   = "-|___________-|_______----|________-",
            ),
        }

        def send(reset, ce, data):
            out = ""
            get = ""

            yield dut.i_data.eq(data.pop(0))
            for i in range(len(ce)+1):
                if i < len(ce):
                    if ce[i] == "|":
                        continue
                    yield dut.reset.eq(reset[i] == '-')
                    yield dut.ce.eq(ce[i] == '-')

                yield

                if i < 1:
                    continue

                out += str((yield dut.o_data))
                o_get = yield dut.o_get
                get += {
                    0: "_",
                    1: "-",
                }[o_get]

                if o_get:
                    if data:
                        yield dut.i_data.eq(data.pop(0))
                    else:
                        yield dut.i_data.eq(0)

                if ce[i-1] == "|":
                    out += "|"
                    get += "|"

            return out   , get

        def stim(width, data, reset, ce, out   , get):
            actual_out, actual_get = yield from send(reset, ce, data)
            self.assertSequenceEqual(out, actual_out)
            self.assertSequenceEqual(get, actual_get)

        for name, vector in sorted(test_vectors.items()):
            with self.subTest(name=name, vector=vector):
                fname = name.replace(' ', '_')
                dut = TxShifter(vector["width"])

                run_simulation(dut, stim(**vector),
                    vcd_name=self.make_vcd_name(testsuffix=fname))


if __name__ == "__main__":
    unittest.main()
