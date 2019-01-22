#!/usr/bin/env python3

import unittest

from migen import *
from migen.genlib import cdc

from ..test.common import BaseUsbTestCase

from .clock import RxClockDataRecovery

class TestRxClockDataRecovery(BaseUsbTestCase):
    def test_basic_recovery(self):
        """
        This test covers basic clock and data recovery.
        """

        def get_output():
            """
            Record data output when line_state_valid is asserted.
            """
            valid = yield dut.line_state_valid
            if valid == 1:
                dj = yield dut.line_state_dj
                dk = yield dut.line_state_dk
                se0 = yield dut.line_state_se0
                se1 = yield dut.line_state_se1

                out = "%d%d%d%d" % (dj, dk, se0, se1)

                return {
                    "1000" : "j",
                    "0100" : "k",
                    "0010" : "0",
                    "0001" : "1",
                }[out]

            else:
                return ""

        def stim(glitch=-1):
            out_seq = ""
            clock = 0
            for bit in seq + "0":
                for i in range(4):
                    if clock != glitch:
                        yield usbp_raw.eq({'j':1,'k':0,'0':0,'1':1}[bit])
                    yield usbn_raw.eq({'j':0,'k':1,'0':0,'1':1}[bit])
                    yield
                    clock += 1
                    out_seq += yield from get_output()
            self.assertEqual(out_seq, "0" + seq)

        test_sequences = [
            "j",
            "k",
            "0",
            "1",
            "jk01",
            "jjjkj0j1kjkkk0k10j0k00011j1k1011"
        ]

        for seq in test_sequences:
            with self.subTest(seq=seq):
                usbp_raw = Signal()
                usbn_raw = Signal()

                dut = RxClockDataRecovery(usbp_raw, usbn_raw)

                run_simulation(
                    dut,
                    stim(),
                    vcd_name=self.make_vcd_name(
                        testsuffix="clock.basic_recovery_%s" % seq),
                )


        long_test_sequences = [
            "jjjkj0j1kjkkk0k10j0k00011j1k1011",
            "kkkkk0k0kjjjk0kkkkjjjkjkjkjjj0kj"
        ]

        for seq in long_test_sequences:
            for glitch in range(0, 32, 8):
                with self.subTest(seq=seq, glitch=glitch):
                    usbp_raw = Signal()
                    usbn_raw = Signal()

                    dut = RxClockDataRecovery(usbp_raw, usbn_raw)

                    run_simulation(
                        dut,
                        stim(glitch),
                        vcd_name=self.make_vcd_name(
                            testsuffix="basic_recovery_%s_%d" % (
                            seq, glitch)),
                    )


if __name__ == "__main__":
    unittest.main()
