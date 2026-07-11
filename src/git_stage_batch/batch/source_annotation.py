"""Temporary compatibility import for batch source annotation."""

import sys as _sys

from .source import annotation as _implementation

_sys.modules[__name__] = _implementation
