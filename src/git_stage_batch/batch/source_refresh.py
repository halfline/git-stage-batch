"""Temporary compatibility import for batch source refresh."""

import sys as _sys

from .source import refresh as _implementation

_sys.modules[__name__] = _implementation
