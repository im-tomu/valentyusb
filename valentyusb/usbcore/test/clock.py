#!/usr/bin/env python3

from migen import *


class CommonTestMultiClockDomain:
    def setUp(self, clock_names):
        self.signals = {}
        self.cycle_count = {}
        self.last_value = {}
        for n in clock_names:
            self.signals[n] = ClockSignal(n)
            self.cycle_count[n] = 0
            self.last_value[n] = 0

    def update_clocks(self):
        for n in self.signals:
            current_value = yield self.signals[n]
            # Run the callback
            if current_value and not self.last_value[n]:
                yield from getattr(self, "on_%s_edge" % n)()
                self.cycle_count[n] += 1
            self.last_value[n] = current_value

    def wait_for_edge(self, name):
        count_last = self.cycle_count[name]
        while True:
            yield from self.update_internal_signals()
            count_next = self.cycle_count[name]
            if count_last != count_next:
                break
            yield

    def update_internal_signals(self):
        yield from self.update_clocks()
