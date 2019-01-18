#!/usr/bin/env python3

import inspect

from migen import *

MIGEN_SIGNALS = ("reset", "ce")

def get_ultimate_caller_modulename():
    """
    Helper to find the ultimate caller's module name (extra level further up
    stack than case where test directly inherits from BaseUsbTestCase).
    """
    caller = inspect.stack()[2]
    module = inspect.getmodule(caller[0])
    return module.__spec__.name

def create_tester(dut_type, **def_args):
    def run(self, **test_args):
        name = self.id()
        self.vcd_name = self.make_vcd_name(
            modulename=get_ultimate_caller_modulename())

        self.inputs = dict()
        self.outputs = dict()
        self.params = set()
        self.dut_args = dict()

        # parse tester definition
        for key in def_args:
            if not key.startswith("i_") and not key.startswith("o_") and key not in MIGEN_SIGNALS:
                self.params.add(key)

        # create dut
        for p in self.params:
            self.dut_args[p] = test_args[p]

        dut = dut_type(**self.dut_args)

        # gather signal
        for key in def_args:
            if key.startswith("i_") or key in MIGEN_SIGNALS:
                self.inputs[key] = getattr(dut, key)
            elif key.startswith("o_"):
                self.outputs[key] = getattr(dut, key)

        # calc num clocks
        clocks = 0
        for i in set(self.inputs.keys()) | set(self.outputs.keys()):
            if isinstance(test_args[i], str):
                clocks = max(clocks, len(test_args[i]))

        # decode stimulus
        def decode(c):
            try:
                return int(c, 16)
            except:
                pass

            if c == "-":
                return 1

            return 0

        # error message debug helper
        def to_waveform(sigs):
            output = ""

            for name in sigs.keys():
                output += "%20s: %s\n" % (name, sigs[name])

            return output



        actual_output = dict()

        # setup stimulus
        def stim():

            for signal_name in self.outputs.keys():
                actual_output[signal_name] = ""

            j = 0
            for i in range(clocks):
                for input_signal in self.inputs.keys():
                    v = test_args[input_signal][i]
                    if v == '|':
                        continue
                    yield self.inputs[input_signal].eq(decode(v))
                if v == '|':
                    continue

                yield

                skip = True
                while True:
                    if test_args[list(self.outputs.keys())[0]][j] != '|':
                        skip = False
                    if not skip:
                        break

                    for output_signal in self.outputs.keys():
                        actual_output[output_signal] += '|'
                    j += 1

                for output_signal in self.outputs.keys():
                    assert len(actual_output[output_signal]) == j

                    actual_value = yield self.outputs[output_signal]
                    actual_output[output_signal] += str(actual_value)

                    expected_value = test_args[output_signal][j]
                    if expected_value == ' ':
                        continue
                    expected_value = decode(expected_value)
                    actual_value = decode(actual_output[output_signal][j])

                    details = "\n"
                    if expected_value != actual_value:
                        details += " %s\n" % (output_signal, )
                        details += "\n"
                        details += "              Actual: %s\n" % (actual_output[output_signal])
                        details += "            Expected: %s\n" % (test_args[output_signal])
                        details += "                      " + (" " * j) + "^\n"
                        details += to_waveform(actual_output)
                    self.assertEqual(expected_value, actual_value, msg = ("%s:%s:%d" % (name, output_signal, j)) + details)

                j += 1



        # run simulation
        run_simulation(dut, stim(), vcd_name=self.vcd_name)

        return actual_output

    return run


def module_tester(dut_type, **def_args):
    def wrapper(class_type):
        class_type.do = create_tester(dut_type, **def_args)
        return class_type

    return wrapper
