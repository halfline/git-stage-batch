"""Compatibility import for line-range views."""

import sys as _sys

from .line_matching import line_range_view as _implementation


_sys.modules[__name__] = _implementation
