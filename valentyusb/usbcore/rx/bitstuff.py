#!/usr/bin/env python3

from migen import *
from migen.fhdl.decorators import ResetInserter

import unittest


@ResetInserter()
class RxBitstuffRemover(Module):
    """RX Bitstuff Removal

    Long sequences of 1's would cause the receiver to lose it's lock on the
    transmitter's clock.  USB solves this with bitstuffing.  A '0' is stuffed
    after every 6 consecutive 1's.  This extra bit is required to recover the
    clock, but it should not be passed on to higher layers in the device.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.9
    https://en.wikipedia.org/wiki/Bit_stuffing

    Clock Domain
    ------------
    usb_12 : 12MHz

    Input Ports
    ------------
    i_data : Signal(1)
        Decoded data bit from USB bus.

    Output Ports
    ------------
    o_data : Signal(1)
        Decoded data bit from USB bus.

    o_stall : Signal(1)
        Indicates the bit stuffer just removed an extra bit, so no data available.

    o_error : Signal(1)
        Indicates there has been a bitstuff error. A bitstuff error occurs
        when there should be a stuffed '0' after 6 consecutive 1's; but instead
        of a '0', there is an additional '1'.  This is normal during IDLE, but
        should never happen within a packet.
        Qualified by valid.
    """

    def __init__(self):
        self.i_data = Signal()

        # This state machine recognizes sequences of 6 bits and drops the 7th
        # bit.  The fsm implements a counter in a series of several states.
        # This is intentional to help absolutely minimize the levels of logic
        # used.
        self.submodules.stuff = stuff = FSM(reset_state="D0")

        drop_bit = Signal(1)

        for i in range(6):
            stuff.act("D%d" % i,
                If(self.i_data,
                    # Receiving '1' increments the bitstuff counter.
                    NextState("D%d" % (i + 1))
                ).Else(
                    # Receiving '0' resets the bitstuff counter.
                    NextState("D0")
                ),
            )

        stuff.act("D6",
            drop_bit.eq(1),
            # Reset the bitstuff counter, drop the data.
            NextState("D0")
        )

        # pass all of the outputs through a pipe stage
        self.o_data = Signal()
        self.o_error = Signal()
        self.o_stall = Signal()

        self.sync += [
            self.o_data.eq(self.i_data),
            self.o_stall.eq(drop_bit),
            self.o_error.eq(drop_bit & self.i_data),
        ]


class TestRxBitstuffRemover(unittest.TestCase):
    def test_bitstuff(self):
        def set_input(dut, r, v):
            yield dut.reset.eq(r == '1')
            yield dut.i_data.eq(v == '1')

        def get_output(dut):
            # Read outputs
            o_data = yield dut.o_data
            o_stall = yield dut.o_stall
            o_error = yield dut.o_error

            if o_error:
                return 'e'
            elif o_stall:
                return's'
            else:
                return str(o_data)

        def send(reset, value):
            assert len(reset) == len(value), (reset, value)
            output = ""
            for i in range(len(value)+1):
                if i < len(value):
                    yield from set_input(dut, reset[i], value[i])

                yield

                if i > 0:
                    if reset[i-1] == '1':
                        output += '_'
                    else:
                        output += yield from get_output(dut)
            return output

        test_vectors = [
            # No bit stuff
            dict(
                reset  = "00000000000000000000",
                value  = "10110111011110111110",
                output = "10110111011110111110",
            ),

            # Bit stuff
            dict(
                reset  = "0000000",
                value  = "1111110",
                output = "111111s",
            ),

            # Bit stuff after reset
            dict(
                reset  = "00010000000",
                value  = "11111111110",
                output = "111_111111s",
            ),

            # Bit stuff error
            dict(
                reset  = "0000000",
                value  = "1111111",
                output = "111111e",
            ),

            # Bit stuff error after reset
            dict(
                reset  = "00010000000",
                value  = "11111111111",
                output = "111_111111e",
            ),

            dict(
                # Multiple bitstuff scenario
                reset  = "000000000000000000000",
                value  = "111111011111101111110",
                output = "111111s111111s111111s",
            ),

            dict(
                # Mixed bitstuff error
                reset  = "000000000000000000000000000000000",
                value  = "111111111111101111110111111111111",
                output = "111111e111111s111111s111111e11111",
            ),

            dict(
                # Idle, Packet, Idle
                reset  = "0000000000000000000000001100000",
                value  = "111110000000111111011101__11111",
                output = "111110000000111111s11101__11111",
            ),

            dict(
                # Idle, Packet, Idle, Packet, Idle
                reset  = "00000000000000000000000011000000000000000000000000000001100000",
                value  = "111110000000111111011101__11111111110000000111111011101__11111",
                output = "111110000000111111s11101__111111e1110000000111111s11101__11111",
            ),

            dict(
                # Captured setup packet (no bitstuff)
                reset  = "000000000000000000000000000000000110",
                value  = "100000001101101000000000000001000__1",
                output = "100000001101101000000000000001000__1"
            )
        ]

        def stim(output, **kw):
            actual_output = yield from send(**kw)
            self.assertEqual(output, actual_output)

        i = 0
        for vector in test_vectors:
            with self.subTest(i=i, vector=vector):
                dut = RxBitstuffRemover()

                run_simulation(dut, stim(**vector), vcd_name="vcd/test_bitstuff_%d.vcd" % i)
                i += 1


if __name__ == "__main__":
    unittest.main()
