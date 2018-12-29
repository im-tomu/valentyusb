#!/usr/bin/env python3

from enum import IntEnum

class PID(IntEnum):
    # USB Packet IDs

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
