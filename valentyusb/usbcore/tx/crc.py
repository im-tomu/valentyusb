#!/usr/bin/env python3

import functools
import operator
import unittest

from migen import *

from migen.fhdl.decorators import CEInserter, ResetInserter

from ..utils.CrcMoose3 import CrcAlgorithm
from ..utils.packet import crc16, encode_data, b
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


def bytes_to_int(d):
    """Convert a list of bytes to an int

    Bytes are in LSB first.

    >>> hex(bytes_to_int([0, 1]))
    '0x100'
    >>> hex(bytes_to_int([1, 2]))
    '0x201'
    """
    v = 0
    for i,d in enumerate(d):
        v |= d << (i*8)
    return v


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
    def __init__(self, data_width, crc_width, polynomial, initial=0):
        self.i_data_payload = Signal(data_width)
        self.i_data_strobe = Signal()
        self.o_crc = Signal(crc_width)
        crc_dat = Signal(data_width)
        crc_cur = Signal(crc_width, reset=initial)
        crc_next = Signal(crc_width, reset_less=True)

        crc_cur_reset_bits = [
            int(i) for i in "{0:0{width}b}".format(
                crc_cur.reset.value,width=crc_width)[::-1]]

        self.comb += [
            crc_dat.eq(self.i_data_payload[::-1]),
            # FIXME: Is XOR ^ initial actually correct here?
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

        crc_next_reset_bits = list(crc_cur_reset_bits)
        for i in range(crc_width):
            to_xor = []
            crc_next_reset_bit_i = []
            for j, use in enumerate(cols_nin[i]):
                if use:
                    to_xor.append(crc_dat[j])
                    crc_next_reset_bit_i.append(0)
            for j, use in enumerate(cols_min[i]):
                if use:
                    to_xor.append(crc_cur[j])
                    crc_next_reset_bit_i.append(crc_cur_reset_bits[j])

            crc_next_reset_bits[i] = functools.reduce(operator.xor, crc_next_reset_bit_i)

            self.comb += [
                crc_next[i].eq(functools.reduce(operator.xor, to_xor)),
            ]

        crc_next_reset_value = int("0b"+"".join(str(i) for i in crc_next_reset_bits[::-1]), 2)
        crc_next.reset.value = crc_next_reset_value


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
