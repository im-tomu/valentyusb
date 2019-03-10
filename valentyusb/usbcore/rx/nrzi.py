#!/usr/bin/env python3

from migen import *
from ..test.common import BaseUsbTestCase

import unittest


class RxNRZIDecoder(Module):
    """RX NRZI decoder.

    In order to ensure there are enough bit transitions for a receiver to recover
    the clock usb uses NRZI encoding.  This module processes the incoming
    dj, dk, se0, and valid signals and decodes them to data values.  It
    also pipelines the se0 signal and passes it through unmodified.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 7.1.8
    https://en.wikipedia.org/wiki/Non-return-to-zero

    Clock Domain
    ------------
    usb_48 : 48MHz

    Input Ports
    -----------
    Input ports are passed in via the constructor.

    i_valid : Signal(1)
        Qualifier for all of the input signals.  Indicates one bit of valid
        data is present on the inputs.

    i_dj : Signal(1)
        Indicates the bus is currently in a Full-Speed J-state.
        Qualified by valid.

    i_dk : Signal(1)
        Indicates the bus is currently in a Full-Speed K-state.
        Qualified by valid.

    i_se0 : Signal(1)
        Indicates the bus is currently in a SE0 state.
        Qualified by valid.

    Output Ports
    ------------
    Output ports are data members of the module. All output ports are flopped.

    o_valid : Signal(1)
        Qualifier for all of the output signals. Indicates one bit of valid
        data is present on the outputs.

    o_data : Signal(1)
        Decoded data bit from USB bus.
        Qualified by valid.

    o_se0 : Signal(1)
        Indicates the bus is currently in a SE0 state.
        Qualified by valid.
    """

    def __init__(self):
        self.i_valid = Signal()
        self.i_dj = Signal()
        self.i_dk = Signal()
        self.i_se0 = Signal()

        # pass all of the outputs through a pipe stage
        self.o_valid = Signal(1)
        self.o_data = Signal(1)
        self.o_se0 = Signal(1)

        if False:
            valid = Signal(1)
            data = Signal(1)

            # simple state machine decodes a JK transition as a '0' and no
            # transition as a '1'.  se0 is ignored.
            self.submodules.nrzi = nrzi = FSM()

            nrzi.act("DJ",
                If(self.i_valid,
                    valid.eq(1),

                    If(self.i_dj,
                        data.eq(1)
                    ).Elif(self.i_dk,
                        data.eq(0),
                        NextState("DK")
                    )
                )
            )

            nrzi.act("DK",
                If(self.i_valid,
                    valid.eq(1),

                    If(self.i_dj,
                        data.eq(0),
                        NextState("DJ")
                    ).Elif(self.i_dk,
                        data.eq(1)
                    )
                )
            )

            self.sync += [
                self.o_valid.eq(valid),
                If(valid,
                    self.o_se0.eq(self.i_se0),
                    self.o_data.eq(data),
                ),
            ]
        else:
            last_data = Signal()
            self.sync += [
                If(self.i_valid,
                    last_data.eq(self.i_dk),
                    self.o_data.eq(~(self.i_dk ^ last_data)),
                    self.o_se0.eq((~self.i_dj) & (~self.i_dk)),
                ),
                self.o_valid.eq(self.i_valid),
            ]
