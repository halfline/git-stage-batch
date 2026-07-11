"""Compatibility import for line-sequence search helpers."""

import sys as _sys

from .line_matching import sequence_search as _implementation


_sys.modules[__name__] = _implementation
