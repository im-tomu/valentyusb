#!/usr/bin/python3

import tempfile
import subprocess
from .sdiff import Differ, getTerminalSize, original_diff
import sys

def assertMultiLineEqualSideBySide(expected, actual, msg):
    # print("data1: {}".format(data1.splitlines(1)))
    if expected == actual:
        return

    withcolor = True
    if not sys.stdout.isatty():
        withcolor = False

    (columns, lines) = getTerminalSize()

    # Print out header
    expected = expected.splitlines(1)
    actual = actual.splitlines(1)
    differ = Differ(linejunk=None, charjunk=None,
                    cutoff=0.1, fuzzy=0,
                    cutoffchar=False, context=5)
    for line in differ.formattext(' ',
                                  None, "expected", None, "actual", columns,
                                      withcolor=withcolor, linediff=None):
        msg = msg + '\n' + line
    for line in differ.formattext(' ',
                                  None, "--------", None, "--------", columns,
                                      withcolor=withcolor, linediff=None):
        msg = msg + '\n' + line

    # Print out body
    lines = original_diff(expected, actual,
                          linejunk=None, charjunk=None,
                          cutoff=0, fuzzy=1,
                          cutoffchar=False, context=5,
                          width=columns,
                          withcolor=withcolor)
    for line in lines:
        msg = msg + '\n' + line

    assert False, msg
