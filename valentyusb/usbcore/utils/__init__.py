#!/usr/bin/env python3


def b(s):
    """Byte string with LSB first into an integer.

    >>> b("1")
    1
    >>> b("01")
    2
    >>> b("101")
    5
    """
    return int(s[::-1], 2)


def nrzi(data, clock_width=4):
    """Converts string of 0s and 1s into NRZI encoded string.

    >>> nrzi("000", 1)
    'JKJ'

    It will do bit stuffing.
    >>> nrzi("1111111111", 1)
    'KKKKKKJJJJJ'

    Support single ended zero
    >>> nrzi("1111111__", 1)
    'KKKKKKJJ__'

    Support pre-encoded mixing.
    >>> nrzi("11kkj11__", 1)
    'KKKKJJJ__'

    Supports wider clock widths
    >>> nrzi("101", 4)
    'KKKKJJJJJJJJ'
    """
    def toggle_state(state):
        if state == 'J':
            return 'K'
        if state == 'K':
            return 'J'
        return state

    state = "K"
    output = ""

    stuffed = []
    i = 0
    for bit in data:
        stuffed.append(bit)
        if bit == '1':
            i += 1
        else:
            i = 0
        if i > 5:
            stuffed.append('0')
            i = 0

    for bit in stuffed:
        if bit == ' ':
            output += bit
            continue

        # only toggle the state on '0'
        if bit == '0':
            state = toggle_state(state)
        elif bit == '1':
            pass
        elif bit in "jk_":
            state = bit.upper()
        else:
            assert False, "Unknown bit %s in %r" % (bit, data)

        output += (state * clock_width)

    return output


if __name__ == "__main__":
    import doctest
    doctest.testmod()
