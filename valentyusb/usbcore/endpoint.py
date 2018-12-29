#!/usr/bin/env python3

from enum import IntEnum


class EndpointType(IntEnum):
    IN = 1
    OUT = 2
    BIDIR = IN | OUT

    @classmethod
    def epaddr(cls, ep_num, ep_dir):
        assert ep_dir != cls.BIDIR
        return ep_num << 1 | (ep_dir == cls.IN)

    @classmethod
    def epnum(cls, ep_addr):
        return ep_addr >> 1

    @classmethod
    def epdir(cls, ep_addr):
        if ep_addr & 0x1 == 0:
            return cls.OUT
        else:
            return cls.IN


class EndpointResponse(IntEnum):
    """
    >>> # Clearing top bit of STALL -> NAK
    >>> assert (EndpointResponse.STALL & EndpointResponse.RESET_MASK) == EndpointResponse.NAK
    """
    STALL = 0b11
    ACK   = 0b00
    NAK   = 0b01
    NONE  = 0b10

    RESET_MASK = 0b01


if __name__ == "__main__":
    import doctest
    doctest.testmod()
