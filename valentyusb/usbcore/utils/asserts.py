#!/usr/bin/python3

import tempfile
import subprocess
from .sdiff import Differ, getTerminalSize, original_diff
import sys

def assertMultiLineEqualSideBySide(data1, data2, msg):
    # print("data1: {}".format(data1.splitlines(1)))
    if data1 == data2:
        return

    withcolor = True
    if not sys.stdout.isatty():
        withcolor = False

    (columns, lines) = getTerminalSize()
    lines = original_diff(data1.splitlines(1), data2.splitlines(1),
                          linejunk=None, charjunk=None,
                          cutoff=0.1, fuzzy=0,
                          cutoffchar=False, context=5,
                          width=columns,
                          withcolor=withcolor)
    for line in lines:
        msg = msg + '\n' + line

    assert False, msg
