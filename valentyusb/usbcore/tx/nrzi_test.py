#!/usr/bin/env python3

import unittest

from migen import *

from .tester import module_tester
from ..test.common import BaseUsbTestCase

from .nrzi import TxNRZIEncoder


@module_tester(
    TxNRZIEncoder,

    i_valid     = (1,),
    i_oe        = (1,),
    i_data      = (1,),

    o_usbp      = (1,),
    o_usbn      = (1,),
    o_oe        = (1,)
)
class TestTxNRZIEncoder(BaseUsbTestCase):
    def test_setup_token(self):
        self.do(
            i_valid = "_|--------|--------|--------|--------|--------",
            i_oe    = "_|--------|--------|--------|--------|--______",
            i_data  = "_|00000001|10110100|00000000|00001000|00______",

            #          XXX|KJKJKJKK|KJJJKKJK|JKJKJKJK|JKJKKJKJ|KJ00JX
            o_oe    = "___|--------|--------|--------|--------|-----_",
            o_usbp  = "   |_-_-_-__|_---__-_|-_-_-_-_|-_-__-_-|_-__- ",
            o_usbn  = "   |-_-_-_--|-___--_-|_-_-_-_-|_-_--_-_|-____ ",
        )


if __name__ == "__main__":
    unittest.main()
