"""Compatibility import for batch ownership replacement line runs."""

import sys as _sys

from .ownership import replacement_line_runs as _implementation


_sys.modules[__name__] = _implementation
