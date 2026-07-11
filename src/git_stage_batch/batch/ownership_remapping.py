"""Compatibility import for batch ownership remapping."""

import sys as _sys

from .ownership import remapping as _implementation


_sys.modules[__name__] = _implementation
