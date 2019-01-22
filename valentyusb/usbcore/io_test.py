#!/usr/bin/env python3

import unittest

from migen import *

from .io import FakeIoBuf

class TestIoBuf(unittest.TestCase):
    pass


class TestFakeIoBuf(unittest.TestCase):
    def setUp(self):
        self.dut = FakeIoBuf()

    def test_recv_idle(self):
        def stim():
            yield
            yield from self.dut.recv('-')
            yield
            self.assertEqual((yield from self.dut.current()), 'J')
            yield
            self.assertEqual((yield from self.dut.current()), 'J')
        run_simulation(self.dut, stim())

    def test_recv_se0(self):
        def stim():
            yield
            yield from self.dut.recv('_')
            yield
            self.assertEqual((yield from self.dut.current()), '_')
            yield
            self.assertEqual((yield from self.dut.current()), '_')
        run_simulation(self.dut, stim())

    def test_recv_se0_alias(self):
        def stim():
            yield
            yield from self.dut.recv('0')
            yield
            self.assertEqual((yield from self.dut.current()), '_')
            yield
            self.assertEqual((yield from self.dut.current()), '_')
        run_simulation(self.dut, stim())

    def test_recv_j(self):
        def stim():
            yield
            yield from self.dut.recv('J')
            yield
            self.assertEqual((yield from self.dut.current()), 'J')
            yield
            self.assertEqual((yield from self.dut.current()), 'J')
        run_simulation(self.dut, stim())

    def test_recv_k(self):
        def stim():
            yield
            yield from self.dut.recv('K')
            yield
            self.assertEqual((yield from self.dut.current()), 'K')
            yield
            self.assertEqual((yield from self.dut.current()), 'K')
        run_simulation(self.dut, stim())


if __name__ == "__main__":
    unittest.main()
