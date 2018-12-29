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
    PID.ACK    2 d2 JJKJJKKK
    PID.NAK    a 5a JJKKKJJK
    PID.STALL  e 1e JJJJJKJK
    """

    # Token pids
    SETUP   = 0b1101
    OUT     = 0b0001
    IN      = 0b1001
    SOF     = 0b0101

    # Data pid
    DATA0   = 0b0011
    DATA1   = 0b1011

    # Handshake pids
    ACK     = 0b0010
    NAK     = 0b1010
    STALL   = 0b1110

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
    >>> PIDTypes.token(PID.DATA0), PIDTypes.data(PID.DATA0), PIDTypes.handshake(PID.DATA0)
    (False, True, False)

    >>> # Handshake PIDs
    >>> PIDTypes.token(PID.ACK), PIDTypes.data(PID.ACK), PIDTypes.handshake(PID.ACK)
    (False, False, True)
    >>> PIDTypes.token(PID.NAK), PIDTypes.data(PID.NAK), PIDTypes.handshake(PID.NAK)
    (False, False, True)
    >>> PIDTypes.token(PID.STALL), PIDTypes.data(PID.STALL), PIDTypes.handshake(PID.STALL)
    (False, False, True)
    """

    TOKEN     = 0b01
    DATA      = 0b11
    HANDSHAKE = 0b10

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


if __name__ == "__main__":
    import doctest
    doctest.testmod()
