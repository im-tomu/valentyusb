#!/usr/bin/env python3


def int_to_bits(i, width=None):
    """Convert an int to list of bits (LSB first).

    l[0]  == LSB
    l[-1] == MSB

    0b10000
      |   |
      |   \\--- LSB
      |
       \\------ MSB

    >>> int_to_bits(0b1)
    [1]
    >>> int_to_bits(0b0)
    [0]
    >>> int_to_bits(0b100)
    [0, 0, 1]
    >>> int_to_bits(0b100, 4)
    [0, 0, 1, 0]
    >>> int_to_bits(0b100, 8)
    [0, 0, 1, 0, 0, 0, 0, 0]
    """
    if width is None:
        width=''
    return [int(i) for i in "{0:0{w}b}".format(i,w=width)[::-1]]


def bits_to_int(bits):
    """Convert a list of bits (LSB first) to an int.

    l[0]  == LSB
    l[-1] == MSB

    0b10000
      |   |
      |   \\--- LSB
      |
       \\------ MSB

    >>> bits_to_int([1])
    1
    >>> bits_to_int([0])
    0
    >>> bin(bits_to_int([0, 0, 1]))
    '0b100'
    >>> bin(bits_to_int([0, 0, 1, 0]))
    '0b100'
    >>> bin(bits_to_int([0, 1, 0, 1]))
    '0b1010'
    >>> bin(bits_to_int([0, 1, 0, 1, 0, 0, 0]))
    '0b1010'
    >>> bin(bits_to_int([0, 0, 0, 0, 0, 1, 0, 1]))
    '0b10100000'
    """
    v = 0
    for i in range(0, len(bits)):
        v |= bits[i] << i
    return v


def int_to_rbits(i, width=None):
    """Convert an int to list of bits (MSB first).

    l[0]  == MSB
    l[-1] == LSB

    0b10000
      |   |
      |   \\--- LSB
      |
       \\------ MSB

    >>> int_to_rbits(0b1)
    [1]
    >>> int_to_rbits(0b0)
    [0]
    >>> int_to_rbits(0b100)
    [1, 0, 0]
    >>> int_to_rbits(0b100, 4)
    [0, 1, 0, 0]
    >>> int_to_rbits(0b100, 8)
    [0, 0, 0, 0, 0, 1, 0, 0]
    """
    if width is None:
        width=''
    return [int(i) for i in "{0:0{w}b}".format(i,w=width)]


def rbits_to_int(rbits):
    """Convert a list of bits (MSB first) to an int.

    l[0]  == MSB
    l[-1] == LSB

    0b10000
      |   |
      |   \\--- LSB
      |
       \\------ MSB

    >>> rbits_to_int([1])
    1
    >>> rbits_to_int([0])
    0
    >>> bin(rbits_to_int([1, 0, 0]))
    '0b100'
    >>> bin(rbits_to_int([1, 0, 1, 0]))
    '0b1010'
    >>> bin(rbits_to_int([1, 0, 1, 0, 0, 0, 0, 0]))
    '0b10100000'
    """
    v = 0
    for i in range(0, len(rbits)):
        v |= rbits[i] << len(rbits)-i-1
    return v


def get_bit(epaddr, v):
    """
    >>> get_bit(0, 0b11)
    True
    >>> get_bit(0, 0b10)
    False
    >>> get_bit(0, 0b101)
    True
    >>> get_bit(1, 0b101)
    False
    """
    return bool(1 << epaddr & v)


def set_bit(current, epaddr, v):
    """
    >>> bin(set_bit(0, 0, 1))
    '0b1'
    >>> bin(set_bit(0, 2, 1))
    '0b100'
    >>> bin(set_bit(0b1000, 2, 1))
    '0b1100'
    >>> bin(set_bit(0b1100, 2, 0))
    '0b1000'
    >>> bin(set_bit(0b1101, 2, 0))
    '0b1001'
    """
    if v:
        return current | 1 << epaddr
    else:
        return current & ~(1 << epaddr)
