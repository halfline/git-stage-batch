"""Temporary compatibility import for batch source advancement."""

import sys as _sys

from .source import advancement as _implementation

_sys.modules[__name__] = _implementation
