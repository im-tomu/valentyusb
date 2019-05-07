#!/usr/bin/env python3

import unittest

from migen import *
from migen.genlib import cdc

from ..test.common import BaseUsbTestCase

from .clock import RxClockDataRecovery

class TestRxClockDataRecovery(BaseUsbTestCase):
    def test_j(self):
        return self.basic_recovery_test("j")

    def test_k(self):
        return self.basic_recovery_test("k")

    def test_0(self):
        return self.basic_recovery_test("0")

    def test_1(self):
        return self.basic_recovery_test("1")

    def test_jk01(self):
        return self.basic_recovery_test("jk01")

    def test_jjjkj0j1kjkkk0k10j0k00011j1k1011(self):
        return self.basic_recovery_test("jjjkj0j1kjkkk0k10j0k00011j1k1011")

    def test_jjjkj0j1kjkkk0k10j0k00011j1k1011(self):
        return self.basic_recovery_test("jjjkj0j1kjkkk0k10j0k00011j1k1011", True)

    def test_kkkkk0k0kjjjk0kkkkjjjkjkjkjjj0kj(self):
        return self.basic_recovery_test("kkkkk0k0kjjjk0kkkkjjjkjkjkjjj0kj", True)

    def basic_recovery_test(self, seq, short_test=True):
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
            while len(out_seq) < len(seq):
                bit = (seq + "0")[clock >> 2]
                if clock != glitch:
                    yield usbp_raw.eq({'j':1,'k':0,'0':0,'1':1}[bit])
                yield usbn_raw.eq({'j':0,'k':1,'0':0,'1':1}[bit])
                yield
                clock += 1
                out_seq += yield from get_output()
            self.assertEqual(out_seq, seq)


        if short_test:
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

        else:
            for glitch in range(0, 32, 1):
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
