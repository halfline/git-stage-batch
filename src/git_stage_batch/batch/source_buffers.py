"""Temporary compatibility import for batch source buffers."""

import sys as _sys

from .source import buffers as _implementation

_sys.modules[__name__] = _implementation
