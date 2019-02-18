# TinyUSB
USB Full-Speed core written in migen/LiteX. This is very much a work in progress.  I'll be uploading a complete version with working examples for TinyFPGA boards as it nears completion.

## Running Simulations

Simulations are the most important part of hardware development.  By presenting a predefined pattern to your module, you can find edgecases before they become a problem.  Strange off-by-one errors can be caught before you spend a lot of time synthesizing and loading onto target hardware.

### Simulation Gripe

I feel the need to include this section here, because the documentation for Python in general is very poor, and unittest is very unintuitive in nature.  It assumes a lot of knowledge that may be obvious to an experienced Python developer, but is very offputting for newcomers to the language.

This repository uses the `unittest` module to drive simulation.  This module is very poorly documented, and this documentation cannot be viewed offline, which can lead to a lot of confusion.  The `pydoc` output notes this:

```
MODULE REFERENCE
    https://docs.python.org/3.6/library/unittest
    
The following documentation is automatically generated from the Python
source files.  It may be incomplete, incorrect or include features that
are considered implementation detail and may vary between Python
implementations.  When in doubt, consult the module reference at the
location listed above.
```

`-- Taken from unittest pydoc`

The `unittest` module treats functions that begin