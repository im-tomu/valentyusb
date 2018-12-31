#!/usr/bin/env python3

from enum import IntEnum


class PID(IntEnum):
    # USB Packet IDs
    """
    >>> bin(PID.SETUP.value)
    '0b1101'
    >>> PID.SETUP.encode()
    'KKKKJJJJJJJJJJJJKKKKKKKKJJJJKKKK'

    >>> for p in PID:
    ...    print("%-10s" % p, "%x" % p.value, "%2x" % p.byte(), p.encode(1))
    PID.SETUP  d 2d KJJJKKJK
    PID.OUT    1 e1 KJKJKKKK
    PID.IN     9 69 KJKKJJJK
    PID.SOF    5 a5 KJJKJJKK
    PID.DATA0  3 c3 KKJKJKKK
    PID.DATA1  b 4b KKJJKJJK
    PID.DATA2  7 87 KKKJKJKK
    PID.MDATA  f  f KKKKJKJK
    PID.ACK    2 d2 JJKJJKKK
    PID.NAK    a 5a JJKKKJJK
    PID.STALL  e 1e JJJJJKJK
    PID.NYET   6 96 JJJKKJKK
    PID.PRE    c 3c JKKKKKJK
    PID.SPLIT  8 78 JKJJJJJK
    PID.PING   4 b4 JKKJJJKK
    PID.RESERVED 0 f0 JKJKKKKK
    """

    # Token pids
    SETUP   = 0b1101
    OUT     = 0b0001
    IN      = 0b1001
    SOF     = 0b0101

    # Data pid
    DATA0   = 0b0011
    DATA1   = 0b1011
    # USB2.0 only
    DATA2   = 0b0111
    MDATA   = 0b1111

    # Handshake pids
    ACK     = 0b0010
    NAK     = 0b1010
    STALL   = 0b1110
    # USB2.0 only
    NYET    = 0b0110

    # USB2.0 only
    PRE      = 0b1100
    ERR      = 0b1100
    SPLIT    = 0b1000
    PING     = 0b0100
    RESERVED = 0b0000

    def byte(self):
        v = self.value
        return v | ((0b1111 ^ v) << 4)

    def encode(self, cycles=4):
        # Prevent cyclic imports by importing here...
        from .utils.packet import nrzi, sync, encode_pid
        return nrzi(sync()+encode_pid(self.value),cycles)[cycles*len(sync()):]


class PIDTypes(IntEnum):
    """
    >>> # Token PIDs
    >>> PIDTypes.token(PID.SETUP), PIDTypes.data(PID.SETUP), PIDTypes.handshake(PID.SETUP)
    (True, False, False)
    >>> PIDTypes.token(PID.OUT), PIDTypes.data(PID.OUT), PIDTypes.handshake(PID.OUT)
    (True, False, False)
    >>> PIDTypes.token(PID.IN), PIDTypes.data(PID.IN), PIDTypes.handshake(PID.IN)
    (True, False, False)
    >>> PIDTypes.token(PID.SOF), PIDTypes.data(PID.SOF), PIDTypes.handshake(PID.SOF)
    (True, False, False)

    >>> # Data PIDs
    >>> PIDTypes.token(PID.DATA0), PIDTypes.data(PID.DATA0), PIDTypes.handshake(PID.DATA0)
    (False, True, False)
    >>> PIDTypes.token(PID.DATA1), PIDTypes.data(PID.DATA1), PIDTypes.handshake(PID.DATA1)
    (False, True, False)
    >>> # USB2.0 Data PIDs
    >>> PIDTypes.token(PID.DATA2), PIDTypes.data(PID.DATA2), PIDTypes.handshake(PID.DATA2)
    (False, True, False)
    >>> PIDTypes.token(PID.MDATA), PIDTypes.data(PID.MDATA), PIDTypes.handshake(PID.MDATA)
    (False, True, False)

    >>> # Handshake PIDs
    >>> PIDTypes.token(PID.ACK), PIDTypes.data(PID.ACK), PIDTypes.handshake(PID.ACK)
    (False, False, True)
    >>> PIDTypes.token(PID.NAK), PIDTypes.data(PID.NAK), PIDTypes.handshake(PID.NAK)
    (False, False, True)
    >>> PIDTypes.token(PID.STALL), PIDTypes.data(PID.STALL), PIDTypes.handshake(PID.STALL)
    (False, False, True)
    >>> # USB2.0 Handshake PIDs
    >>> PIDTypes.token(PID.NYET), PIDTypes.data(PID.NYET), PIDTypes.handshake(PID.NYET)
    (False, False, True)

    >>> # Special PIDs
    >>> PIDTypes.token(PID.PRE), PIDTypes.data(PID.PRE), PIDTypes.handshake(PID.PRE)
    (False, False, False)
    """

    TOKEN     = 0b0001
    DATA      = 0b0011
    HANDSHAKE = 0b0010
    SPECIAL   = 0b0000

    TYPE_MASK = 0b0011

    @staticmethod
    def token(p):
        assert isinstance(p, PID), repr(p)
        return (p & PIDTypes.TYPE_MASK) == PIDTypes.TOKEN

    @staticmethod
    def data(p):
        assert isinstance(p, PID), repr(p)
        return (p & PIDTypes.TYPE_MASK) == PIDTypes.DATA

    @staticmethod
    def handshake(p):
        assert isinstance(p, PID), repr(p)
        return (p & PIDTypes.TYPE_MASK) == PIDTypes.HANDSHAKE

    @staticmethod
    def special(p):
        assert isinstance(p, PID), repr(p)
        return (p & PIDTypes.TYPE_MASK) == PIDTypes.SPECIAL


if __name__ == "__main__":
    import doctest
    doctest.testmod()
