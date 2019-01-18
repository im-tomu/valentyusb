#!/usr/bin/env python3

from migen import *
from migen.fhdl.decorators import CEInserter, ResetInserter
from ..test.common import BaseUsbTestCase

import unittest

@CEInserter()
@ResetInserter()
class RxShifter(Module):
    """RX Shifter

    A shifter is responsible for shifting in serial bits and presenting them
    as parallel data.  The shifter knows how many bits to shift and has
    controls for resetting the shifter.

    Clock Domain
    ------------
    usb_12 : 12MHz

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Number of bits to shift in.

    Input Ports
    -----------
    i_data : Signal(1)
        Serial input data.

    Output Ports
    ------------
    o_data : Signal(width)
        Shifted in data.

    o_put : Signal(1)
        Asserted for one clock once the register is full.
    """
    def __init__(self, width):
        self.i_data = Signal()
        self.i_data = Signal()

        self.o_data = Signal(width)
        self.o_put = Signal()

        # Instead of using a counter, we will use a sentinel bit in the shift
        # register to indicate when it is full.
        shift_reg = Signal(width+1, reset=0b1)

        self.sync += [
            self.o_put.eq(shift_reg[width]),
            If(shift_reg[width],
                self.o_data.eq(shift_reg[0:width]),
                shift_reg.eq(Cat(self.i_data, shift_reg.reset[0:width])),
            ).Else(
                shift_reg.eq(Cat(self.i_data, shift_reg[0:width])),
            ),
        ]


class TestRxShifter(BaseUsbTestCase):
    def test_shifter(self):
        test_vectors = [
            # 0
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "1|00000000|",
                put      = "_|_______-|",
                output   = [0b00000000]
            ),
            # 1
            dict(
                # basic shift in
                reset    = "-|________|||________|",
                data     = "1|00000001|||00000001|",
                put      = "_|_______-|||_______-|",
                output   = [0b00000001,0b00000001]
            ),
            # 2
            dict(
                # basic shift in
                reset    = "-|________|||________|",
                data     = "1|10000000|||10000000|",
                put      = "_|_______-|||_______-|",
                output   = [0b10000000,0b10000000]
            ),
            # 3
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "1|11111111|",
                put      = "_|_______-|",
                output   = [0b11111111]
            ),
            # 4
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "1|10000000|",
                put      = "_|_______-|",
                output   = [0b10000000]
            ),
            # 5
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "1|00000001|",
                put      = "_|_______-|",
                output   = [0b00000001]
            ),
            # 6
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "1|01111110|",
                put      = "_|_______-|",
                output   = [0b01111110]
            ),
            # 7
            dict(
                # basic shift in
                reset    = "-|________|",
                data     = "0|01110100|",
                put      = "_|_______-|",
                output   = [0b01110100]
            ),
            # 8
            dict(
                # basic shift in, 2 bytes
                reset    = "-|________|||________|",
                data     = "0|01110100|||10101000|",
                put      = "_|_______-|||_______-|",
                output   = [0b01110100,0b10101000]
            ),
            # 9
            dict(
                # multiple resets
                reset    = "-|________|______-|___-|________|",
                data     = "0|01110100|0011010|0111|10101000|",
                put      = "_|_______-|_______|____|_______-|",
                output   = [0b01110100,           0b10101000]
            ),
            # 10
            dict(
                # multiple resets (tight timing)
                reset    = "-|________|-|________|",
                data     = "0|01110100|1|00101000|",
                put      = "_|_______-|_|_______-|",
                output   = [0b01110100,0b00101000]
            ),
        ]

        actual_output = []
        def send(reset, data , put=None, output=None):
            for i in range(len(data)+2):
                if i < len(data):
                    if data[i] == '|':
                        assert reset[i] == '|', reset[i]
                        assert put[i]   == '|', put[i]
                        continue
                    yield dut.reset.eq(reset[i] == '-')
                    yield dut.i_data.eq(data[i] == '1')
                yield
                o_put = yield dut.o_put
                if o_put:
                    last_output = yield dut.o_data
                    actual_output.append(last_output)

        for i, vector in enumerate(test_vectors):
            with self.subTest(i=i, vector=vector):
                dut = RxShifter(8)

                actual_output.clear()
                run_simulation(dut, send(**vector),
                    vcd_name=self.make_vcd_name("usbcore.rx.shifter.%d" % i))
                self.assertListEqual(vector['output'], actual_output)


if __name__ == "__main__":
    unittest.main()
