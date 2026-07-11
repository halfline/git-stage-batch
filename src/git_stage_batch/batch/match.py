"""Compatibility import for line matching."""

import sys as _sys

from .line_matching import match as _implementation


_sys.modules[__name__] = _implementation
