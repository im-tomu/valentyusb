#!/usr/bin/env python3

import unittest

from migen import *


class Raw(Instance.PreformattedParam):
    def __init__(self, value):
        self.value = value


class IoBuf(Module):
    def __init__(self, usbp_pin, usbn_pin, usb_pullup_pin=None):
        # tx/rx io interface
        self.usb_tx_en = Signal()
        self.usb_p_tx = Signal()
        self.usb_n_tx = Signal()

        self.usb_p_rx = Signal()
        self.usb_n_rx = Signal()

        self.usb_p_rx_io = Signal()
        self.usb_n_rx_io = Signal()

        self.usb_pullup = Signal()
        if usb_pullup_pin is not None:
            self.comb += [
                usb_pullup_pin.eq(self.usb_pullup),
            ]

        #######################################################################
        #######################################################################
        #### Mux the USB +/- pair with the TX and RX paths
        #######################################################################
        #######################################################################
        self.comb += [
            If(self.usb_tx_en,
                self.usb_p_rx.eq(0b1),
                self.usb_n_rx.eq(0b0)
            ).Else(
                self.usb_p_rx.eq(self.usb_p_rx_io),
                self.usb_n_rx.eq(self.usb_n_rx_io)
            )
        ]

        self.specials += [
            Instance(
                "SB_IO",
                p_PIN_TYPE = Raw("6'b101001"),
                p_PULLUP = 0b0,

                io_PACKAGE_PIN = usbp_pin,
                i_OUTPUT_ENABLE = self.usb_tx_en,
                i_D_OUT_0 = self.usb_p_tx,
                o_D_IN_0 = self.usb_p_rx_io
            ),

            Instance(
                "SB_IO",
                p_PIN_TYPE = Raw("6'b101001"),
                p_PULLUP = 0b0,

                io_PACKAGE_PIN = usbn_pin,
                i_OUTPUT_ENABLE = self.usb_tx_en,
                i_D_OUT_0 = self.usb_n_tx,
                o_D_IN_0 = self.usb_n_rx_io
            )
        ]


class FakeIoBuf(Module):
    def __init__(self):
        self.usb_pullup = Signal()

        self.usb_p = Signal()
        self.usb_n = Signal()

        self.usb_tx_en = Signal()
        self.usb_p_tx = Signal()
        self.usb_n_tx = Signal()

        self.usb_p_rx = Signal()
        self.usb_n_rx = Signal()

        self.usb_p_rx_io = Signal()
        self.usb_n_rx_io = Signal()

        self.comb += [
            If(self.usb_tx_en,
                self.usb_p_rx.eq(0b1),
                self.usb_n_rx.eq(0b0)
            ).Else(
                self.usb_p_rx.eq(self.usb_p_rx_io),
                self.usb_n_rx.eq(self.usb_n_rx_io)
            ),
        ]
        self.comb += [
            If(self.usb_tx_en,
                self.usb_p.eq(self.usb_p_tx),
                self.usb_n.eq(self.usb_n_tx),
            ).Else(
                self.usb_p.eq(self.usb_p_rx),
                self.usb_n.eq(self.usb_n_rx),
            ),
        ]

    def recv(self, v):
        tx_en = yield self.usb_tx_en
        assert not tx_en, "Currently transmitting!"

        if v == '0' or v == '_':
            # SE0 - both lines pulled low
            yield self.usb_p_rx_io.eq(0)
            yield self.usb_n_rx_io.eq(0)
        elif v == '1':
            # SE1 - illegal, should never occur
            yield self.usb_p_rx_io.eq(1)
            yield self.usb_n_rx_io.eq(1)
        elif v == '-' or v == 'I':
            # Idle
            yield self.usb_p_rx_io.eq(1)
            yield self.usb_n_rx_io.eq(0)
        elif v == 'J':
            yield self.usb_p_rx_io.eq(1)
            yield self.usb_n_rx_io.eq(0)
        elif v == 'K':
            yield self.usb_p_rx_io.eq(0)
            yield self.usb_n_rx_io.eq(1)
        else:
            assert False, "Unknown value: %s" % v

    def current(self):
        usb_p = yield self.usb_p
        usb_n = yield self.usb_n
        values = (usb_p, usb_n)

        if values == (0, 0):
            return '_'
        elif values == (1, 1):
            return '1'
        elif values == (1, 0):
            return 'J'
        elif values == (0, 1):
            return 'K'
        else:
            assert False, values


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
