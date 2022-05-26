from migen import *

from migen.fhdl.decorators import ResetInserter

@ResetInserter()
class RxPullUpDetect(Module):

    def __init__(self, threshold=1000):
        self.i_d_p = Signal()
        self.i_d_n = Signal()
        self.i_tx_en = Signal()
        self.o_j_pullup_detect = Signal()
        self.o_k_pullup_detect = Signal()

        cnt = Signal(max=threshold+1)
        pull_mode_cur = Signal(2)
        pull_mode_last = Signal(2)
        pull_mode_latched = Signal(2)

        self.comb += [
            self.o_j_pullup_detect.eq(pull_mode_latched[0]),
            self.o_k_pullup_detect.eq(pull_mode_latched[1])
        ]

        self.sync += [
            pull_mode_last.eq(pull_mode_cur),
            pull_mode_cur.eq(Cat(self.i_d_p, self.i_d_n)),
            If (self.i_tx_en | (pull_mode_cur != pull_mode_last) |
                (pull_mode_last == 0b11) |
                ((pull_mode_last ^ pull_mode_latched) == 0b11),
                cnt.eq(0)
            ).Elif (cnt == threshold,
                pull_mode_latched.eq(pull_mode_last)
            ).Else (
                cnt.eq(cnt + 1)
            )
        ]
