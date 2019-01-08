#!/usr/bin/env python3

import tempfile

def write_gtkwave_file(vcd_filename):

    basename, ext = os.path.splitext(vcd_filename)
    gtkw_filename = basename + '.gtkw'
    topdir = os.path.abspath(os.path.join('..', os.path.dirname(__file__)))

    with open(gtkw_filename, 'w') as f:
        f.write("""
[*]
[*] GTKWave Analyzer v3.3.86 (w)1999-2017 BSI
[*] Fri Jan  4 10:39:59 2019
[*]
[dumpfile] "{vcd_filename}"
[dumpfile_mtime] "Fri Jan  4 09:20:43 2019"
[dumpfile_size] 31260
[savefile] "{gtkw_filename}"
[timestart] 0
[size] 1920 1019
[pos] -1 -1
*-19.442974 0 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1 -1
[sst_width] 204
[signals_width] 296
[sst_expanded] 1
[sst_vpaned_height] 302
@10800028
[transaction_args] ""
^<1 {topdir}/utils/dec-usb.sh
#{usb} usbn usbp
@200
-
-
-
-
@28
usbp
usbn
@1001200
-group_end
@200
-
@10800028
[transaction_args] ""
^<1 {topdir}/utils/dec-usb.sh
#{usb} i_usbp i_usbn
@200
-
-
-
@29
i_usbp
@28
i_usbn
@1001200
-group_end
[pattern_trace] 1
[pattern_trace] 0
""".format(**locals()))


def add_vcd_timescale(filename, timescale=435):
    tempfile = tempfile.SpooledTemporaryFile()
    with open(filename) as f:
        data = f.read()
    with open(filename, 'w') as f:
        f.write("$timescale %ips $end\n" % timescale)
        f.write(data)
