"""Compatibility import for line comparison helpers."""

import sys as _sys

from .line_matching import comparison as _implementation


_sys.modules[__name__] = _implementation
