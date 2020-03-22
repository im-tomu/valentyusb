#!/usr/bin/env python3

import sys
from setuptools import setup
from setuptools import find_packages

setup(
    name="valentyusb",
    version="0.3.4dev",
    description="FPGA USB stack written in LiteX",
    long_description=open("README.md").read(),
    author="Luke Valenty",
    author_email="luke@tinyfpga.com",
    url="https://github.com/im-tomu/valentyusb",
    download_url="https://github.com/im-tomu/valentyusb",
    license="BSD",
    platforms=["Any"],
    keywords=["HDL", "FPGA", "USB"],
    classifiers=[
        "Development Status :: Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: BSD License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
    ],
    packages=find_packages(exclude=("test-suite*", "sim*", "docs*")),
    install_requires=["litex"],
    

)
