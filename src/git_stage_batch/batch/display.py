"""Compatibility import for batch ownership display lines."""

import sys as _sys

from .ownership import display_lines as _implementation


_sys.modules[__name__] = _implementation
