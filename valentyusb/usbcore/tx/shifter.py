#!/usr/bin/env python3

import unittest

from migen import *

from migen.fhdl.decorators import CEInserter, ResetInserter

from utils import b


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

    o_data_strobe : Signal(1)
        Asserted the cycle after the shifter loads in i_data.

    """
    def __init__(self, width):
        self.reset = Signal()
        self.i_data = Signal(width)
        self.o_data = Signal(1)
        self.o_data_strobe = Signal(1)

        shifter = Signal(width)
        pos = Signal(width, reset=0b1)

        empty = Signal(1)
        self.sync += [
            self.o_data.eq(shifter[0]),
            pos.eq(pos >> 1),
            shifter.eq(shifter >> 1),
            If(empty |self.reset,
                shifter.eq(self.i_data),
                pos.eq(1 << (width-1)),
                self.o_data_strobe.eq(1),
            ).Else(
                self.o_data_strobe.eq(0),
            )
        ]
        self.comb += [
            empty.eq(pos[0]),
        ]


class TestTxShifter(unittest.TestCase):
    def test_shifter(self):
        test_vectors = {
            "basic shift out 1": dict(
                width  = 8,
                data   = [b("00000001"), b("00000001"), b("00000001"), 0],
                reset  = "-|________|________|________",
                ce     = "-|--------|--------|--------",
                output = "0|00000001|00000001|00000001",
                strobe = "-|_______-|_______-|_______-",
            ),
            "basic shift out 2": dict(
                width  = 8,
                data   = [b("10000000"), b("10000000"), b("10000000"), 0],
                reset  = "-|________|________|________",
                ce     = "-|--------|--------|--------",
                output = "0|10000000|10000000|10000000",
                strobe = "-|_______-|_______-|_______-",
            ),
            "basic shift out 3": dict(
                width  = 8,
                data   = [b("01100110"), b("10000001"), b("10000000"), 0],
                reset  = "-|________|________|________",
                ce     = "-|--------|--------|--------",
                output = "0|01100110|10000001|10000000",
                strobe = "-|_______-|_______-|_______-",
            ),
            "stall shift out 1": dict(
                width  = 8,
                data   = [b("00000001"), b("00000001"), b("00000001"), 0],
                reset  = "-|_________|________|________",
                ce     = "-|--_------|--------|--------",
                output = "0|000000001|00000001|00000001",
                strobe = "-|________-|_______-|_______-",
            ),
            "stall shift out 2": dict(
                width  = 8,
                data   = [b("10000000"), b("10000000"), b("10000000"), 0],
                reset  = "-|_________|________|________",
                ce     = "-|---_-----|--------|--------",
                output = "0|100000000|10000000|10000000",
                strobe = "-|________-|_______-|_______-",
            ),
            "stall shift out 3": dict(
                width  = 8,
                data   = [b("01100110"), b("10000001"), b("10000000"), 0],
                reset  = "-|_________|________|________",
                ce     = "-|---_-----|--------|--------",
                output = "0|011100110|10000001|10000000",
                strobe = "-|________-|_______-|_______-",
            ),
            "mutlistall shift out 1": dict(
                width  = 8,
                data   = [b("00000001"), b("00000001"), b("00000001"), 0],
                reset  = "-|___________|_________|_________",
                ce     = "-|--___------|--------_|----_----",
                output = "0|00000000001|000000011|000000001",
                strobe = "-|__________-|_______--|________-",
            ),
            "mutlistall shift out 2": dict(
                width  = 8,
                data   = [b("10000000"), b("10000000"), b("10000000"), 0],
                reset  = "-|____________|________|__________",
                ce     = "-|---____-----|--------|-_----_---",
                output = "0|100000000000|10000000|1100000000",
                strobe = "-|___________-|_______-|_________-",
            ),
            "mutlistall shift out 3": dict(
                width  = 8,
                data   = [b("01100110"), b("10000001"), b("10000000"), 0],
                reset  = "-|____________|___________|_________",
                ce     = "-|---____-----|--------___|-_-------",
                output = "0|011111100110|10000001111|110000000",
                strobe = "-|___________-|_______----|________-",
            ),
        }

        def send(reset, ce, data):
            output = ""
            strobe = ""

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

                output += str((yield dut.o_data))
                o_strobe = yield dut.o_data_strobe
                strobe += {
                    0: "_",
                    1: "-",
                }[o_strobe]

                if o_strobe:
                    if data:
                        yield dut.i_data.eq(data.pop(0))
                    else:
                        yield dut.i_data.eq(0)

                if ce[i-1] == "|":
                    output += "|"
                    strobe += "|"

            return output, strobe

        def stim(width, data, reset, ce, output, strobe):
            actual_output, actual_strobe = yield from send(reset, ce, data)
            self.assertSequenceEqual(output, actual_output)
            self.assertSequenceEqual(strobe, actual_strobe)

        for name, vector in sorted(test_vectors.items()):
            with self.subTest(name=name, vector=vector):
                fname = name.replace(' ', '_')
                dut = TxShifter(vector["width"])

                run_simulation(dut, stim(**vector), vcd_name="vcd/test_tx_shifter_%s.vcd" % fname)


if __name__ == "__main__":
    unittest.main()
