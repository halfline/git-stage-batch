"""Compatibility import for batch ownership hunk line ranges."""

import sys as _sys

from .ownership import hunk_line_ranges as _implementation


_sys.modules[__name__] = _implementation
