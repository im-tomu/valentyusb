#!/usr/bin/env python3

from .packet import *
from ..pid import *


def pp_packet(p):
    """
    >>> print(pp_packet(wrap_packet(handshake_packet(PID.ACK))))
    ----
    KKKK 1 Sync
    JJJJ 2 Sync
    KKKK 3 Sync
    JJJJ 4 Sync
    KKKK 5 Sync
    JJJJ 6 Sync
    KKKK 7 Sync
    KKKK 8 Sync
    ----
    JJJJ 1 PID (PID.ACK)
    JJJJ 2 PID
    KKKK 3 PID
    JJJJ 4 PID
    JJJJ 5 PID
    KKKK 6 PID
    KKKK 7 PID
    KKKK 8 PID
    ----
    ____ SE0
    ____ SE0
    JJJJ END

    >>> print(pp_packet(wrap_packet(token_packet(PID.SETUP, 0, 0))))
    ----
    KKKK 1 Sync
    JJJJ 2 Sync
    KKKK 3 Sync
    JJJJ 4 Sync
    KKKK 5 Sync
    JJJJ 6 Sync
    KKKK 7 Sync
    KKKK 8 Sync
    ----
    KKKK 1 PID (PID.SETUP)
    JJJJ 2 PID
    JJJJ 3 PID
    JJJJ 4 PID
    KKKK 5 PID
    KKKK 6 PID
    JJJJ 7 PID
    KKKK 8 PID
    ----
    JJJJ 1 Address
    KKKK 2 Address
    JJJJ 3 Address
    KKKK 4 Address
    JJJJ 5 Address
    KKKK 6 Address
    JJJJ 7 Address
    KKKK 1 Endpoint
    ----
    JJJJ 2 Endpoint
    KKKK 3 Endpoint
    JJJJ 4 Endpoint
    KKKK 1 CRC5
    KKKK 2 CRC5
    JJJJ 3 CRC5
    KKKK 4 CRC5
    JJJJ 5 CRC5
    ----
    ____ SE0
    ____ SE0
    JJJJ END
    >>> print(pp_packet(wrap_packet(data_packet(PID.DATA0, [5, 6]))))
    ----
    KKKK 1 Sync
    JJJJ 2 Sync
    KKKK 3 Sync
    JJJJ 4 Sync
    KKKK 5 Sync
    JJJJ 6 Sync
    KKKK 7 Sync
    KKKK 8 Sync
    ----
    KKKK 1 PID (PID.DATA0)
    KKKK 2 PID
    JJJJ 3 PID
    KKKK 4 PID
    JJJJ 5 PID
    KKKK 6 PID
    KKKK 7 PID
    KKKK 8 PID
    ----
    KKKK
    JJJJ
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ
    KKKK
    ----
    JJJJ
    JJJJ
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ
    KKKK
    ----
    KKKK  1 CRC16
    JJJJ  2 CRC16
    JJJJ  3 CRC16
    JJJJ  4 CRC16
    JJJJ  5 CRC16
    JJJJ  6 CRC16
    JJJJ  7 CRC16
    KKKK  8 CRC16
    ----
    KKKK  9 CRC16
    JJJJ 10 CRC16
    JJJJ 11 CRC16
    JJJJ 12 CRC16
    JJJJ 13 CRC16
    KKKK 14 CRC16
    JJJJ 15 CRC16
    KKKK 16 CRC16
    ----
    ____ SE0
    ____ SE0
    JJJJ END


    >>> # Requires bit stuffing!
    >>> print(pp_packet(wrap_packet(data_packet(PID.DATA0, [0x1]))))
    ----
    KKKK 1 Sync
    JJJJ 2 Sync
    KKKK 3 Sync
    JJJJ 4 Sync
    KKKK 5 Sync
    JJJJ 6 Sync
    KKKK 7 Sync
    KKKK 8 Sync
    ----
    KKKK 1 PID (PID.DATA0)
    KKKK 2 PID
    JJJJ 3 PID
    KKKK 4 PID
    JJJJ 5 PID
    KKKK 6 PID
    KKKK 7 PID
    KKKK 8 PID
    ----
    KKKK
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ
    ----
    JJJJ  1 CRC16
    KKKK  2 CRC16
    JJJJ  3 CRC16
    KKKK  4 CRC16
    JJJJ  5 CRC16
    KKKK  6 CRC16
    JJJJ  7 CRC16
    JJJJ  8 CRC16
    ----
    JJJJ  9 CRC16
    JJJJ 10 CRC16
    JJJJ 11 CRC16
    JJJJ 12 CRC16
    JJJJ 13 CRC16
    KKKK    Bitstuff
    KKKK 14 CRC16
    KKKK 15 CRC16
    JJJJ 16 CRC16
    ----
    ____ SE0
    ____ SE0
    JJJJ END

    >>> print(pp_packet(wrap_packet(data_packet(PID.DATA0, [0x1]))[:96]))
    ----
    KKKK 1 Sync
    JJJJ 2 Sync
    KKKK 3 Sync
    JJJJ 4 Sync
    KKKK 5 Sync
    JJJJ 6 Sync
    KKKK 7 Sync
    KKKK 8 Sync
    ----
    KKKK 1 PID (PID.DATA0)
    KKKK 2 PID
    JJJJ 3 PID
    KKKK 4 PID
    JJJJ 5 PID
    KKKK 6 PID
    KKKK 7 PID
    KKKK 8 PID
    ----
    KKKK
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ
    KKKK
    JJJJ END
    >>> print(pp_packet(wrap_packet(sof_packet(12))))
    ----
    KKKK 1 Sync
    JJJJ 2 Sync
    KKKK 3 Sync
    JJJJ 4 Sync
    KKKK 5 Sync
    JJJJ 6 Sync
    KKKK 7 Sync
    KKKK 8 Sync
    ----
    KKKK 1 PID (PID.SOF)
    JJJJ 2 PID
    JJJJ 3 PID
    KKKK 4 PID
    JJJJ 5 PID
    JJJJ 6 PID
    KKKK 7 PID
    KKKK 8 PID
    ----
    KKKK  1 Frame #
    JJJJ  2 Frame #
    KKKK  3 Frame #
    JJJJ  4 Frame #
    KKKK  5 Frame #
    JJJJ  6 Frame #
    KKKK  7 Frame #
    JJJJ  8 Frame #
    ----
    JJJJ  9 Frame #
    JJJJ 10 Frame #
    KKKK 11 Frame #
    KKKK 1 CRC5
    JJJJ 2 CRC5
    KKKK 3 CRC5
    JJJJ 4 CRC5
    JJJJ 5 CRC5
    ----
    ____ SE0
    ____ SE0
    JJJJ END

    """

    output = []
    chunks = [p[i:i+4] for i in range(0, len(p), 4)]

    class BitStuff:
        def __init__(self):
            self.i = 0

        def __call__(self, chunk):
            if self.i == 7:
                self.i = 0
                if chunk == 'KKKK':
                    output.extend([chunk, '    Bitstuff\n'])
                else:
                    output.extend([chunk, '    Bitstuff ERROR!\n'])
                return True

            if chunk == 'JJJJ':
                self.i += 1
            else:
                self.i = 0
            return False

    class Seperator:
        def __init__(self):
            self.i = 0

        def __call__(self, chunk):
            if self.i % 8 == 0:
                output.append('----\n')
            self.i += 1
            return False


    class Sync:
        def __init__(self):
            self.i = 0

        def __call__(self, chunk):
            if self.i > 7:
                return False
            self.i += 1
            output.extend([chunk, ' %i Sync\n' % self.i])
            return True


    class Pid:
        def __init__(self):
            self.done = False
            self.pid_chunks = []
            self.type = None

            self.encoded_pids = {}
            for p in PID:
                self.encoded_pids[p.encode()] = p

        def __call__(self, chunk):
            if self.done:
                return False

            self.pid_chunks.append(chunk)
            if len(self.pid_chunks) < 8:
                return True

            self.done = True
            self.type = self.encoded_pids.get("".join(self.pid_chunks), 'ERROR')

            for i, chunk in enumerate(self.pid_chunks):
                if i == 0:
                    output.extend([chunk, ' %i PID (%s)\n' % (1, self.type)])
                else:
                    output.extend([chunk, ' %i PID\n' % (i+1,)])

            return True


    class SOF:
        def __init__(self, pid):
            self.pid = pid
            self.i = 0
            self.state = 'FRAME NUMBER'

        def __call__(self, chunk):
            if self.pid.type != PID.SOF:
                return False

            self.i += 1
            if self.state == 'FRAME NUMBER':
                output.extend([chunk, ' %2i Frame #\n' % self.i])
                if self.i == 11:
                    self.state = 'CRC5'
                    self.i = 0
            elif self.state == 'CRC5':
                output.extend([chunk, ' %i CRC5\n' % self.i])
                if self.i == 5:
                    self.state = "DATA"
                    self.i = 0
            else:
                output.extend([chunk, ' ERROR!\n'])

            return True

        def finish(self):
            pass


    class Data:
        def __init__(self, pid):
            self.done = False
            self.pid = pid
            self.last16 = []

        def __call__(self, chunk):
            if self.pid.type not in (PID.DATA0, PID.DATA1):
                return False

            self.last16.append(chunk)
            output.append(None)

            if len(self.last16) > 16:
                self.patch(self.last16.pop(0)+'\n')

            return True

        def patch(self, s):
            assert isinstance(s, str), s
            p = output.index(None)
            output[p] = s

        def finish(self):
            if output.count(None) == 0:
                return False

            assert output.count(None) == len(self.last16), (output.count(None), len(self.last16))
            if len(self.last16) == 16:
                for i, chunk in enumerate(self.last16):
                    self.patch(chunk+' %2i CRC16\n' % (i+1,))
            else:
                for i, chunk in enumerate(self.last16):
                    self.patch(chunk+'\n')
            assert output.count(None) == 0


    class Token:
        def __init__(self, pid):
            self.pid = pid
            self.i = 0
            self.state = 'ADDRESS'

        def __call__(self, chunk):
            if self.pid.type in (PID.DATA0, PID.DATA1):
                return False

            self.i += 1

            if self.state == 'ADDRESS':
                output.extend([chunk, ' %i Address\n' % self.i])
                if self.i == 7:
                    self.state = "ENDPOINT"
                    self.i = 0
            elif self.state == 'ENDPOINT':
                output.extend([chunk, ' %i Endpoint\n' % self.i])
                if self.i == 4:
                    self.state = "CRC5"
                    self.i = 0
            elif self.state == 'CRC5':
                output.extend([chunk, ' %i CRC5\n' % self.i])
                if self.i == 5:
                    self.state = "DATA"
                    self.i = 0
            elif self.state == 'DATA':
                output.extend([chunk, ' ERROR\n'])

            return True


    class End:
        def __init__(self):
            pass

        def __call__(self, chunk):
            if chunk == '____':
                output.extend([chunk, ' SE0\n'])
                return True
            if len(chunks) == 0:
                output.extend([chunk, ' END\n'])
                return True
            return False

    printers = []
    printers.append(BitStuff())
    printers.append(Seperator())
    printers.append(Sync())
    pid_printer = Pid()
    printers.append(pid_printer)
    printers.append(End())
    printers.append(SOF(pid_printer))
    printers.append(Data(pid_printer))
    printers.append(Token(pid_printer))

    while len(chunks) > 0:
        chunk = chunks.pop(0)
        for printer in printers:
            if printer(chunk):
                break
        else:
            output.extend([chunk, ' ERROR!\n'])

    for p in printers:
        if not hasattr(p, "finish"):
            continue
        p.finish()

    assert output.count(None) == 0, output
    output[-1] = output[-1][:-1]
    return "".join(output)


if __name__ == "__main__":
    import doctest
    doctest.testmod()
