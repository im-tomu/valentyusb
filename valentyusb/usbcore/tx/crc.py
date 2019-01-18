#!/usr/bin/env python3

import functools
import operator
import unittest

from migen import *

from migen.fhdl.decorators import CEInserter, ResetInserter

from ..utils.CrcMoose3 import CrcAlgorithm
from ..utils.packet import crc5, crc16, encode_data, b
from .shifter import TxShifter
from .tester import module_tester
from ..test.common import BaseUsbTestCase


@CEInserter()
@ResetInserter()
class TxSerialCrcGenerator(Module):
    """
    Transmit CRC Generator

    TxSerialCrcGenerator generates a running CRC.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 8.3.5
    https://en.wikipedia.org/wiki/Cyclic_redundancy_check

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Width of the CRC.

    polynomial : int
        CRC polynomial in integer form.

    initial : int
        Initial value of the CRC register before data starts shifting in.

    Input Ports
    ------------
    i_data : Signal(1)
        Serial data to generate CRC for.

    Output Ports
    ------------
    o_crc : Signal(width)
        Current CRC value.

    """
    def __init__(self, width, polynomial, initial):

        self.i_data = Signal()

        crc = Signal(width, reset=initial)
        crc_invert = Signal(1)

        self.comb += [
            crc_invert.eq(self.i_data ^ crc[width - 1])
        ]

        for i in range(width):
            rhs_data = None
            if i == 0:
                rhs_data = crc_invert
            else:
                if (polynomial >> i) & 1:
                    rhs_data = crc[i - 1] ^ crc_invert
                else:
                    rhs_data = crc[i - 1]

            self.sync += [
                crc[i].eq(rhs_data)
            ]

        self.o_crc = Signal(width)

        for i in range(width):
            self.comb += [
                self.o_crc[i].eq(1 ^ crc[width - i - 1]),
            ]


@module_tester(
    TxSerialCrcGenerator,

    width       = None,
    polynomial  = None,
    initial     = None,

    reset       = (1,),
    ce          = (1,),
    i_data      = (1,),

    o_crc       = ("width",)
)
class TestTxSerialCrcGenerator(BaseUsbTestCase):
    def test_token_crc5_zeroes(self):
        self.do(
            width      = 5,
            polynomial = 0b00101,
            initial    = 0b11111,

            reset      = "-_______________",
            ce         = "__-----------___",
            i_data     = "  00000000000   ",
            o_crc      = "             222"
        )

    def test_token_crc5_zeroes_alt(self):
        self.do(
            width      = 5,
            polynomial = 0b00101,
            initial    = 0b11111,

            reset      = "-______________",
            ce         = "_-----------___",
            i_data     = " 00000000000   ",
            o_crc      = "            222"
        )

    def test_token_crc5_nonzero(self):
        self.do(
            width      = 5,
            polynomial = 0b00101,
            initial    = 0b11111,

            reset      = "-______________",
            ce         = "_-----------___",
            i_data     = " 01100000011   ",
            o_crc      = "            ccc"
        )

    def test_token_crc5_nonzero_stall(self):
        self.do(
            width      = 5,
            polynomial = 0b00101, # polynomial = (5, 2, 0)
            initial    = 0b11111, # seed = 0x1F

            reset      = "-_____________________________",
            ce         = "_-___-___-___-___-___------___",
            i_data     = " 0   1   111101110111000011   ",
            o_crc      = "                           ccc"
        )

    def test_data_crc16_nonzero(self):
        self.do(
            width      = 16,
            polynomial = 0b1000000000000101, # polynomial = (16, 15, 2, 0)
            initial    = 0b1111111111111111, # seed = 0xFFFF

            reset      = "-________________________________________________________________________",
            ce         = "_--------_--------_--------_--------_--------_--------_--------_--------_",
            i_data     = " 00000001 01100000 00000000 10000000 00000000 00000000 00000010 00000000 ",
            o_crc      =("                                                                        *", [0x94dd])
        )


def cols(rows):
    """
    >>> a = [
    ...  [1, 2],
    ...  ['a', 'b'],
    ...  [4, 5],
    ... ]
    >>> for c in cols(a):
    ...   print(c)
    [1, 'a', 4]
    [2, 'b', 5]
    >>> a = [
    ...  [1, 2, 3],
    ...  ['a', 'b', 'c'],
    ... ]
    >>> for c in cols(a):
    ...   print(c)
    [1, 'a']
    [2, 'b']
    [3, 'c']

    """
    all_c = []
    for ci in range(len(rows[0])):
        all_c.append([])
    for ci in range(len(rows[0])):
        for ri in range(len(rows)):
            assert len(rows[ri]) == len(all_c), "len(%r) != %i" % (rows[ri], len(all_c))
            all_c[ci].append(rows[ri][ci])
    return all_c


def lfsr_serial_shift_crc(lfsr_poly, lfsr_cur, data):
    """

    shift_by == num_data_bits
    len(data_cur) == num_data_bits
    >>> for i in range(5):
    ...   l = [0]*5; l[i] = 1
    ...   r = lfsr_serial_shift_crc(
    ...      lfsr_poly=[0,0,1,0,1], # (5, 2, 0)
    ...      lfsr_cur=l,
    ...      data=[0,0,0,0],
    ...   )
    ...   print("Min[%i] =" % i, r)
    Min[0] = [1, 0, 0, 0, 0]
    Min[1] = [0, 0, 1, 0, 1]
    Min[2] = [0, 1, 0, 1, 0]
    Min[3] = [1, 0, 1, 0, 0]
    Min[4] = [0, 1, 1, 0, 1]
    >>> for i in range(4):
    ...   d = [0]*4; d[i] = 1
    ...   r = lfsr_serial_shift_crc(
    ...      lfsr_poly=[0,0,1,0,1], # (5, 2, 0)
    ...      lfsr_cur=[0,0,0,0,0],
    ...      data=d,
    ...   )
    ...   print("Nin[%i] =" % i, r)
    Nin[0] = [0, 0, 1, 0, 1]
    Nin[1] = [0, 1, 0, 1, 0]
    Nin[2] = [1, 0, 1, 0, 0]
    Nin[3] = [0, 1, 1, 0, 1]

    """
    lfsr_poly = lfsr_poly[::-1]
    data = data[::-1]

    shift_by = len(data)
    lfsr_poly_size = len(lfsr_poly)
    assert lfsr_poly_size > 1
    assert len(lfsr_cur) == lfsr_poly_size

    lfsr_next = list(lfsr_cur)
    for j in range(shift_by):
        lfsr_upper_bit = lfsr_next[lfsr_poly_size-1]
        for i in range(lfsr_poly_size-1, 0, -1):
            if lfsr_poly[i]:
                lfsr_next[i] = lfsr_next[i-1] ^ lfsr_upper_bit ^ data[j]
            else:
                lfsr_next[i] = lfsr_next[i-1]
        lfsr_next[0] = lfsr_upper_bit ^ data[j]
    return list(lfsr_next[::-1])


def print_matrix(crc_width, cols_nin, cols_min):
    """
    >>> crc_width = 5
    >>> data_width = 4
    >>> poly_list = [0, 0, 1, 0, 1]
    >>> _, cols_nin, cols_min = build_matrix(poly_list, data_width)
    >>> print_matrix(crc_width, cols_nin, cols_min)
       0 d[ 0],      ,      , d[ 3],      , c[ 1],      ,      , c[ 4]
       1      , d[ 1],      ,      ,      ,      , c[ 2],      ,
       2 d[ 0],      , d[ 2], d[ 3],      , c[ 1],      , c[ 3], c[ 4]
       3      , d[ 1],      , d[ 3],      ,      , c[ 2],      , c[ 4]
       4      ,      , d[ 2],      , c[ 0],      ,      , c[ 3],
    """
    for i in range(crc_width):
        text_xor = []
        for j, use in enumerate(cols_nin[i]):
            if use:
                text_xor.append('d[%2i]' % j)
            else:
                text_xor.append('     ')
        for j, use in enumerate(cols_min[i]):
            if use:
                text_xor.append('c[%2i]' % j)
            else:
                text_xor.append('     ')
        print("{:4d} {}".format(i, ", ".join("{:>5s}".format(x) for x in text_xor).rstrip()))


def build_matrix(lfsr_poly, data_width):
    """
    >>> print("\\n".join(build_matrix([0,0,1,0,1], 4)[0]))
    lfsr([0, 0, 1, 0, 1], [0, 0, 0, 0, 0], [1, 0, 0, 0]) = [0, 0, 1, 0, 1]
    lfsr([0, 0, 1, 0, 1], [0, 0, 0, 0, 0], [0, 1, 0, 0]) = [0, 1, 0, 1, 0]
    lfsr([0, 0, 1, 0, 1], [0, 0, 0, 0, 0], [0, 0, 1, 0]) = [1, 0, 1, 0, 0]
    lfsr([0, 0, 1, 0, 1], [0, 0, 0, 0, 0], [0, 0, 0, 1]) = [0, 1, 1, 0, 1]
    <BLANKLINE>
    lfsr([0, 0, 1, 0, 1], [1, 0, 0, 0, 0], [0, 0, 0, 0]) = [1, 0, 0, 0, 0]
    lfsr([0, 0, 1, 0, 1], [0, 1, 0, 0, 0], [0, 0, 0, 0]) = [0, 0, 1, 0, 1]
    lfsr([0, 0, 1, 0, 1], [0, 0, 1, 0, 0], [0, 0, 0, 0]) = [0, 1, 0, 1, 0]
    lfsr([0, 0, 1, 0, 1], [0, 0, 0, 1, 0], [0, 0, 0, 0]) = [1, 0, 1, 0, 0]
    lfsr([0, 0, 1, 0, 1], [0, 0, 0, 0, 1], [0, 0, 0, 0]) = [0, 1, 1, 0, 1]
    <BLANKLINE>
    Mout[4] = [0, 0, 1, 0] [1, 0, 0, 1, 0]
    Mout[3] = [0, 1, 0, 1] [0, 0, 1, 0, 1]
    Mout[2] = [1, 0, 1, 1] [0, 1, 0, 1, 1]
    Mout[1] = [0, 1, 0, 0] [0, 0, 1, 0, 0]
    Mout[0] = [1, 0, 0, 1] [0, 1, 0, 0, 1]
    """
    lfsr_poly_size = len(lfsr_poly)

    # data_width*lfsr_polysize matrix == lfsr(0,Nin)
    rows_nin = []

    # (a) calculate the N values when Min=0 and Build NxM matrix
    #  - Each value is one hot encoded (there is only one bit)
    #  - IE N=4, 0x1, 0x2, 0x4, 0x8
    #  - Mout = F(Nin,Min=0)
    #  - Each row contains the results of (a)
    #  - IE row[0] == 0x1, row[1] == 0x2
    #  - Output is M-bit wide (CRC width)
    #  - Each column of the matrix represents an output bit Mout[i] as a function of Nin
    info = []
    for i in range(data_width):
        # lfsr_cur = [0,...,0] = Min
        lfsr_cur = [0,]*lfsr_poly_size
        # data = [0,..,1,..,0] = Nin
        data = [0,]*data_width
        data[i] = 1
        # Calculate the CRC
        rows_nin.append(lfsr_serial_shift_crc(lfsr_poly, lfsr_cur, data))
        info.append("lfsr(%r, %r, %r) = %r" % (lfsr_poly, lfsr_cur, data, rows_nin[-1]))
    assert len(rows_nin) == data_width
    cols_nin = cols(rows_nin)[::-1]

    # lfsr_polysize*lfsr_polysize matrix == lfsr(Min,0)
    info.append("")
    rows_min = []
    for i in range(lfsr_poly_size):
        # lfsr_cur = [0,..,1,...,0] = Min
        lfsr_cur = [0,]*lfsr_poly_size
        lfsr_cur[i] = 1
        # data = [0,..,0] = Nin
        data = [0,]*data_width
        # Calculate the crc
        rows_min.append(lfsr_serial_shift_crc(lfsr_poly, lfsr_cur, data))
        info.append("lfsr(%r, %r, %r) = %r" % (lfsr_poly, lfsr_cur, data, rows_min[-1]))
    assert len(rows_min) == lfsr_poly_size
    cols_min = cols(rows_min)[::-1]

    # (c) Calculate CRC for the M values when Nin=0 and Build MxM matrix
    #  - Each value is one hot encoded
    #  - Mout = F(Nin=0,Min)
    #  - Each row contains results from (7)
    info.append("")
    for i in range(data_width, -1, -1):
        info.append("Mout[%i] = %r %r" % (i, cols_nin[i], cols_min[i]))

    return info, cols_nin, cols_min


@ResetInserter()
class TxParallelCrcGenerator(Module):
    """
    Transmit CRC Generator

    TxParallelCrcGenerator generates a running CRC.

    https://www.pjrc.com/teensy/beta/usb20.pdf, USB2 Spec, 8.3.5
    https://en.wikipedia.org/wiki/Cyclic_redundancy_check

    Parameters
    ----------
    Parameters are passed in via the constructor.

    width : int
        Width of the CRC.

    polynomial : int
        CRC polynomial in integer form.

    initial : int
        Initial value of the CRC register before data starts shifting in.

    Input Ports
    ------------
    i_data_payload : Signal(8)
        Byte wide data to generate CRC for.

    i_data_strobe : Signal(1)
        Strobe signal for the payload.

    Output Ports
    ------------
    o_crc : Signal(width)
        Current CRC value.

    """
    def __init__(self, data_width, crc_width, polynomial, initial):
        self.i_data_payload = Signal(data_width)
        self.i_data_strobe = Signal()
        self.o_crc = Signal(crc_width)

        crc_dat = Signal(data_width)
        crc_cur = Signal(crc_width, reset=initial)
        crc_next = Signal(crc_width)

        self.comb += [
            crc_dat.eq(self.i_data_payload[::-1]),
            self.o_crc.eq(crc_cur[::-1] ^ initial),
        ]

        self.sync += [
            If(self.i_data_strobe,
                crc_cur.eq(crc_next),
            ),
        ]

        poly_list = []
        for i in range(crc_width):
            poly_list.insert(0, polynomial >> i & 0x1)
        assert len(poly_list) == crc_width

        _, cols_nin, cols_min = build_matrix(poly_list, data_width)

        for i in range(crc_width):
            to_xor = []
            for j, use in enumerate(cols_nin[i]):
                if use:
                    to_xor.append(crc_dat[j])
            for j, use in enumerate(cols_min[i]):
                if use:
                    to_xor.append(crc_cur[j])

            self.comb += [
                crc_next[i].eq(functools.reduce(operator.xor, to_xor)),
            ]


def o(d):
    """
    >>> hex(o([0, 1]))
    '0x100'
    >>> hex(o([1, 2]))
    '0x201'
    """
    v = 0
    for i,d in enumerate(d):
        v |= d << (i*8)
    return v


class TestTxParallelCrcGenerator(BaseUsbTestCase):
    def sim(self, name, dut, in_data, expected_crc):
        def stim():
            yield dut.i_data_strobe.eq(1)
            for d in in_data:
                yield dut.i_data_payload.eq(d)
                yield
                o_crc = yield dut.o_crc
                print("{0} {1:04x} {1:016b} {2:04x} {2:016b}".format(name, expected_crc, o_crc))
            yield
            o_crc = yield dut.o_crc
            print("{0} {1:04x} {1:016b} {2:04x} {2:016b}".format(name, expected_crc, o_crc))
            self.assertEqual(hex(expected_crc), hex(o_crc))

        run_simulation(dut, stim(), vcd_name=self.make_vcd_name())

    def sim_crc16(self, in_data):
        expected_crc = o(crc16(in_data))
        dut = TxParallelCrcGenerator(
            crc_width  = 16,
            polynomial = 0b1000000000000101,
            initial    = 0b1111111111111111,
            data_width = 8,
        )
        mask = 0xff
        self.assertSequenceEqual(in_data, [x & mask for x in in_data])
        self.sim("crc16", dut, in_data, expected_crc)

    def sim_crc5(self, in_data):
        expected_crc = crc5(in_data)[0]
        dut = TxParallelCrcGenerator(
            crc_width  = 5,
            polynomial = 0b00101,
            initial    = 0b11111,
            data_width = 4,
        )
        mask = 0x0f
        self.assertSequenceEqual(in_data, [x & mask for x in in_data])
        self.sim("crc5", dut, in_data, expected_crc)

    def test_token_crc5_zeroes(self):
        self.sim_crc5([0, 0])

    def test_token_crc5_nonzero1(self):
        self.sim_crc5([b("0110"), b("0000")])

    def test_data_crc16_nonzero1(self):
        self.sim_crc16([
            b("00000001"), b("01100000"), b("00000000"), b("10000000"),
            b("00000000"), b("00000000"), b("00000010"), b("00000000"),
        ])

    def test_data_crc16_nonzero2(self):
        self.sim_crc16([
            0b00000001, 0b01100000, 0b00000000, 0b10000000,
            0b00000000, 0b00000000, 0b00000010, 0b00000000,
        ])


class TxCrcPipeline(Module):
    def __init__(self):
        self.i_data_payload = Signal(8)
        self.o_data_ack = Signal()
        self.o_crc16 = Signal(16)

        self.reset = reset = Signal()
        reset_n1 = Signal()
        reset_n2 = Signal()
        self.ce = ce = Signal()

        self.sync += [
            reset_n2.eq(reset_n1),
            reset_n1.eq(reset),
        ]

        self.submodules.shifter = shifter = TxShifter(width=8)
        self.comb += [
            shifter.i_data.eq(self.i_data_payload),
            shifter.reset.eq(reset),
            shifter.ce.eq(ce),
            self.o_data_ack.eq(shifter.o_get),
        ]

        self.submodules.crc = crc_calc = TxSerialCrcGenerator(
            width      = 16,
            polynomial = 0b1000000000000101,
            initial    = 0b1111111111111111,
        )
        self.comb += [
            crc_calc.i_data.eq(shifter.o_data),
            crc_calc.reset.eq(reset_n2),
            crc_calc.ce.eq(ce),
            self.o_crc16.eq(crc_calc.o_crc),
        ]


class TestCrcPipeline(BaseUsbTestCase):
    maxDiff=None

    def sim(self, data):
        expected_crc = crc16(data)

        dut = TxCrcPipeline()
        dut.expected_crc = Signal(16)
        def stim():
            MAX = 1000
            yield dut.expected_crc[:8].eq(expected_crc[0])
            yield dut.expected_crc[8:].eq(expected_crc[1])
            yield dut.reset.eq(1)
            yield dut.ce.eq(1)
            for i in range(MAX+1):
                if i > 10:
                    yield dut.reset.eq(0)

                ack = yield dut.o_data_ack
                if ack:
                    if len(data) == 0:
                        yield dut.ce.eq(0)
                        for i in range(5):
                            yield
                        crc16_value = yield dut.o_crc16

                        encoded_expected_crc = encode_data(expected_crc)
                        encoded_actual_crc = encode_data([crc16_value & 0xff, crc16_value >> 8])
                        self.assertSequenceEqual(encoded_expected_crc, encoded_actual_crc)
                        return
                    data.pop(0)
                if len(data) > 0:
                    yield dut.i_data_payload.eq(data[0])
                else:
                    yield dut.i_data_payload.eq(0xff)
                yield
            self.assertLess(i, MAX)

        run_simulation(dut, stim(), vcd_name=self.make_vcd_name())

    def test_00000001_byte(self):
        self.sim([0b00000001])

    def test_10000000_byte(self):
        self.sim([0b10000000])

    def test_00000000_byte(self):
        self.sim([0])

    def test_11111111_byte(self):
        self.sim([0xff])

    def test_10101010_byte(self):
        self.sim([0b10101010])

    def test_zero_bytes(self):
        self.sim([0, 0, 0])

    def test_sequential_bytes(self):
        self.sim([0, 1, 2])

    def test_sequential_bytes2(self):
        self.sim([0, 1])

    def test_sequential_bytes3(self):
        self.sim([1, 0])


if __name__ == "__main__":
    import doctest
    doctest.testmod()
    unittest.main()
