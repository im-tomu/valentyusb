#!/bin/bash
export PYTHONHASHSEED=1
exec `dirname $0`/gtkwave-sigrok-filter.py -P usb_signalling:signalling=full-speed:dm=usb_d_n:dp=usb_d_p,usb_packet:signalling=full-speed
