#!/bin/bash

find valentyusb/usbcore/cpu/unififo* -type f -iname "*_test.py" -print0 | sort -z | while IFS= read -r -d $'\0' line; do
    test=`echo $line | sed -e 's/\//\./g' | sed -e 's/\.py$//'`
    cmd='python3 ../../lxbuildenv.py -r -m unittest -v '$test
    echo "$cmd"
    `$cmd`
done

