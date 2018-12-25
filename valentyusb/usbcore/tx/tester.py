#!/usr/bin/env python3

from migen import *


def create_tester(dut_type, **def_args):
    def run(self, **test_args):
        name = self.id()

        self.inputs = dict()
        self.outputs = dict()
        self.params = set()
        self.dut_args = dict()

        # parse tester definition
        for key in def_args:
            if not key.startswith("i_") and not key.startswith("o_"):
                self.params.add(key)

        # create dut
        for p in self.params:
            self.dut_args[p] = test_args[p]

        dut = dut_type(**self.dut_args)

        # gather signal
        for key in def_args:
            if key.startswith("i_"):
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

            for i in range(clocks):
                for input_signal in self.inputs.keys():
                    yield self.inputs[input_signal].eq(decode(test_args[input_signal][i]))

                yield

                for output_signal in self.outputs.keys():
                    actual_value = yield self.outputs[output_signal]
                    actual_output[output_signal] += str(actual_value)


                    if isinstance(test_args[output_signal], tuple):
                        if test_args[output_signal][0][i] == '*':
                            expected_value = decode(test_args[output_signal][1].pop(0))

                    elif test_args[output_signal] is not None:
                        if test_args[output_signal][i] != ' ':
                            expected_value = decode(test_args[output_signal][i])
                            details = "\n"
                            if actual_value != expected_value:
                                details += "            Expected: %s\n" % (test_args[output_signal])
                                details += "                      " + (" " * i) + "^\n"
                                details += to_waveform(actual_output)
                            self.assertEqual(actual_value, expected_value, msg = ("%s:%s:%d" % (name, output_signal, i)) + details)


        # run simulation
        run_simulation(dut, stim(), vcd_name="vcd/%s.vcd" % name)

        return actual_output

    return run


def module_tester(dut_type, **def_args):
    def wrapper(class_type):
        class_type.do = create_tester(dut_type, **def_args)
        return class_type

    return wrapper
