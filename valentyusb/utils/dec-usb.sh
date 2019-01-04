#!/bin/bash

exec `dirname $0`/gtkwave-sigrok-filter.py -P usb_signalling:signalling=full-speed,usb_packet:signalling=full-speed
