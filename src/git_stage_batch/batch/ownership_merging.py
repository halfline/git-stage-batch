"""Compatibility import for batch ownership merging."""

import sys as _sys

from .ownership import merging as _implementation


_sys.modules[__name__] = _implementation
