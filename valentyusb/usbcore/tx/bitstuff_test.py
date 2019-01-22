#!/usr/bin/env python3

import unittest

from migen import *

from migen.fhdl.decorators import CEInserter, ResetInserter

from .bitstuff import TxBitstuffer
from .tester import module_tester
from ..test.common import BaseUsbTestCase


@module_tester(
    TxBitstuffer,

    i_data      = (1,),

    o_stall     = (1,),
    o_data      = (1,),
)
class TestTxBitstuffer(BaseUsbTestCase):
    def test_passthrough(self):
        self.do(
            i_data  = "--___---__",

            o_stall = "__________",
            o_data  = "_--___---_",
        )

    def test_passthrough_se0(self):
        self.do(
            i_data  = "--___---__",

            o_stall = "__________",
            o_data  = "_--___---_",
        )

    def test_bitstuff(self):
        self.do(
            i_data  = "---------__",

            o_stall = "______-____",
            o_data  = "_------_--_",
        )

    def test_bitstuff_input_stall(self):
        self.do(
            i_data  = "---------",

            o_stall = "______-__",
            o_data  = "_------_-",
        )

    def test_bitstuff_se0(self):
        self.do(
            i_data  = "---------__-",

            o_stall = "______-_____",
            o_data  = "_------_--__",
        )

    def test_bitstuff_at_eop(self):
        self.do(
            i_data  = "-------__",

            o_stall = "______-__",
            o_data  = "_------__",
        )

    def test_multi_bitstuff(self):
        self.do(
            i_data  = "----------------",

            o_stall = "______-______-__",
            o_data  = "_------_------_-",
        )


if __name__ == "__main__":
    unittest.main()
