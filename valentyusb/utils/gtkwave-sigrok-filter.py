#!/usr/bin/env python3

import subprocess
import sys
import tempfile
import random
import re


COLORS = [
	"alice blue",
	"antique white",
	"aquamarine",
	"azure",
	"beige",
	"bisque",
	"black",
	"blanched almond",
	"blue",
	"blue violet",
	"brown",
	"burlywood",
	"cadet blue",
	"chartreuse",
	"chocolate",
	"coral",
	"cornflower blue",
	"cornsilk",
	"cyan",
	"dark blue",
	"dark cyan",
	"dark goldenrod",
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
	"floral white",
	"forest green",
	"gainsboro",
	"ghost white",
	"gold",
	"goldenrod",
	"green",
	"green yellow",
	"honeydew",
	"hot pink",
	"indian red",
	"ivory",
	"khaki",
	"lavender",
	"lavender blush",
	"lawn green",
	"lemon chiffon",
	"light blue",
	"light coral",
	"light cyan",
	"light goldenrod",
	"light goldenrod yellow",
	"light green",
	"light pink",
	"light salmon",
	"light sea green",
	"light sky blue",
	"light slate blue",
	"light steel blue",
	"light yellow",
	"lime green",
	"linen",
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
	"mint cream",
	"misty rose",
	"moccasin",
	"navajo white",
	"navy",
	"navy blue",
	"old lace",
	"olive drab",
	"orange",
	"orange red",
	"orchid",
	"pale goldenrod",
	"pale green",
	"pale turquoise",
	"pale violet red",
	"papaya whip",
	"peach puff",
	"peru",
	"pink",
	"plum",
	"powder blue",
	"purple",
	"red",
	"rosy brown",
	"royal blue",
	"saddle brown",
	"salmon",
	"sandy brown",
	"sea green",
	"seashell",
	"sienna",
	"sky blue",
	"slate blue",
	"snow",
	"spring green",
	"steel blue",
	"tan",
	"thistle",
	"tomato",
	"turquoise",
	"violet",
	"violet red",
	"wheat",
	"white",
	"white smoke",
	"yellow",
	"yellow green",
]

def pick_color():
	return COLORS[random.randrange(0, len(COLORS)-1)]
	

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

			for k in data.keys():
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
	sys.exit(main(*sys.argv))
