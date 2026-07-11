"""Compatibility import for line-sequence equality helpers."""

import sys as _sys

from .line_matching import sequence_equality as _implementation


_sys.modules[__name__] = _implementation
