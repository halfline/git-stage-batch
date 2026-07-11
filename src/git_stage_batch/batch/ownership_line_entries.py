"""Compatibility import for batch ownership line entries."""

import sys as _sys

from .ownership import line_entries as _implementation


_sys.modules[__name__] = _implementation
