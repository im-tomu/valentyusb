#!/usr/bin/python3

import tempfile
import subprocess

def assertMultiLineEqualSideBySide(data1, data2, msg):
    if data1 == data2:
        return
    f1 = tempfile.NamedTemporaryFile()
    f1.write(data1.encode('utf-8'))
    f1.flush()

    f2 = tempfile.NamedTemporaryFile()
    f2.write(data2.encode('utf-8'))
    f2.flush()

    p = subprocess.Popen(["sdiff", f1.name, f2.name], stdout=subprocess.PIPE)
    stdout, stderr = p.communicate()
    diff = stdout.decode('utf-8')

    f1.close()
    f2.close()
    assert False, msg+'\n'+diff
