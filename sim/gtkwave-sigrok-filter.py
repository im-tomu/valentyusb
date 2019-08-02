#!/usr/bin/env python3

"""
gtkwave-sigrok-filter.py

Use as a 'Transaction Filter Process' in gtkwave to apply signal

Usage:
 - Group input signals in gtkwave with F4
 - Apply this script as a 'Transaction Filter Process' (Right click / Data Format)
   (note that sometime options don't work and you have to create a wrapper
    shell script calling this python file with options you want)
 - To get more decoding rows, add blank traces right below the first decoded
   trace.

Options:
 - All options given to this script are passes as is to sigrok-cli, so
   refer to sigrok-cli doc for how to use protocol decoders
 - Examples:

Wrapper script for USB full speed decoding :

```
#!/bin/bash
exec `dirname $0`/gtkwave-sigrok-filter.py -P usb_signalling:signalling=full-speed,usb_packet:signalling=full-speed
```

Wrapper script for SPI decoding :

```
#!/bin/bash
exec `dirname $0`/gtkwave-sigrok-filter.py -P spi
```


Copyright (C) 2019 Sylvain Munaut
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.
2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR
ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
(INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
(INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS
SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

The views and conclusions contained in the software and documentation are those
of the authors and should not be interpreted as representing official policies,
either expressed or implied, of anyone.
"""

import subprocess
import sys
import tempfile
import random
import re


COLORS = [
	"black",
	"blue",
	"blue violet",
	"brown",
	"burlywood",
	"cadet blue",
	"chocolate",
	"cornflower blue",
	"dark blue",
	"dark cyan",
	"dark green",
	"dark khaki",
	"dark magenta",
	"dark olive green",
	"dark orange",
	"dark orchid",
	"dark red",
	"dark salmon",
	"dark sea green",
	"dark slate blue",
	"dark turquoise",
	"dark violet",
	"deep pink",
	"deep sky blue",
	"dodger blue",
	"firebrick",
	"forest green",
	"gold",
	"hot pink",
	"indian red",
	"khaki",
	"magenta",
	"maroon",
	"medium aquamarine",
	"medium blue",
	"medium orchid",
	"medium purple",
	"medium sea green",
	"medium slate blue",
	"medium spring green",
	"medium turquoise",
	"medium violet red",
	"midnight blue",
	"navy",
	"navy blue",
	"olive drab",
	"orange",
	"orange red",
	"orchid",
	"pale violet red",
	"peru",
	"pink",
	"plum",
	"purple",
	"red",
	"rosy brown",
	"royal blue",
	"saddle brown",
	"salmon",
	"sandy brown",
	"sienna",
	"sky blue",
	"slate blue",
	"steel blue",
	"tan",
	"thistle",
	"tomato",
	"turquoise",
	"violet",
	"violet red",
	"yellow green",
]

def pick_color():
	color = COLORS[random.randrange(0, len(COLORS)-1)]
	return color


def get_decoders_infos(args):
	# Return value
	rv = {}

	# Run sigrok-cli
	pipe = subprocess.Popen([
			'sigrok-cli',  '--show',
		] + list(args),
		stdout=subprocess.PIPE,
	)
	text = pipe.communicate()[0].decode('utf-8')

	# Parse output
	cur = None
	active = False
	for l in text.splitlines():
		if l.startswith('ID: '):
			cur = rv.setdefault(l[4:], ({},{}))
		elif l == 'Annotation rows:':
			active = True
		elif not l.startswith('-'):
			active = False
		elif active:
			m = re.match('^- (.*) \((.*)\): (.*)$', l)
			for cn in m.group(3).split(','):
				cur[0][cn.strip()] = (m.group(1), pick_color())
			cur[1][m.group(1)] = m.group(2)

	return rv


def main(argv0, *args):
	decoders = get_decoders_infos(args)
	fh_in  = sys.stdin
	fh_out = sys.stdout
	fh_err = sys.stderr
	with tempfile.NamedTemporaryFile() as fh_temp:
		# Repeat ...
		while True:
			# Read input until we get a full VCD input
			while True:
				l = fh_in.readline()
				if not l:
					return 0

				fh_temp.write(l.encode('utf-8'))

				if l.startswith('$comment data_end'):
					break

			fh_temp.flush()

			# Feed this to sigrok-cli and get output
			data = {}

			pipe = subprocess.Popen([
					'sigrok-cli', '-l', '4', '-O', 'ascii',
					'-i', fh_temp.name, '--input-format', 'vcd',
					'--protocol-decoder-samplenum',
				] + list(args),
				stdout=subprocess.PIPE,
				universal_newlines=True
			)
			text = pipe.communicate()[0]

			for l in text.splitlines():
				# Parse
				l_t, l_d, l_c, l_v = l.strip().split(' ', 3)

				l_t = [int(x) for x in l_t.split('-')]	# Time Span
				l_d = l_d.strip(':')					# Decoder id
				l_c = l_c.strip(':')					# Annotation class
				l_v = l_v.strip()						# Value

				# Grab decoder infos
				d_id = l_d.split('-',1)[0]
				rowmap = decoders[d_id][0]

				# Map to a row
				row_id, color = rowmap[l_c]

				# Select one of the value
				l_v = re.split('" "', l_v[1:-1])
				v = l_v[(len(l_v)-1)//2]

				# Save the start/stop event
				e = data.setdefault((l_d, row_id), {})
				if l_t[1] not in e:
					e[l_t[1]] = None
				e[l_t[0]] = '?%s?%s' % (color, v)

			# Output
			first = True

			for k in sorted(data.keys()):
				if not first:
					fh_out.write("$next\n")
				first = False

				trace_name = k[0] + '/' + decoders[k[0].split('-',1)[0]][1][k[1]]
				fh_out.write("$name %s\n" % (trace_name,))

				for t in sorted(data[k].keys()):
					v = data[k][t]
					fh_out.write("#%d %s\n" % (t, v if (v is not None) else ''))

			fh_out.write("$finish\n")
			fh_out.flush()

			# Reset
			fh_temp.seek(0)
			fh_temp.truncate()
			del data


if __name__ == '__main__':
	try:
		sys.exit(main(*sys.argv))
	except Exception as e:
		sys.stderr.write("Exception occurred: {}\n".format(e))
		sys.stdout.write("$finish\n")
		sys.exit(1)
