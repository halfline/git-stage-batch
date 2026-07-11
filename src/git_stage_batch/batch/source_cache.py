"""Temporary compatibility import for batch source cache."""

import sys as _sys

from .source import cache as _implementation

_sys.modules[__name__] = _implementation
