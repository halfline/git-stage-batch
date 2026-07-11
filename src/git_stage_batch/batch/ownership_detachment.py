"""Compatibility import for batch ownership detachment."""

import sys as _sys

from .ownership import detachment as _implementation


_sys.modules[__name__] = _implementation
