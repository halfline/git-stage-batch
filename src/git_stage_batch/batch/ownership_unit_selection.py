"""Compatibility import for batch ownership unit selection."""

import sys as _sys

from .ownership import unit_selection as _implementation


_sys.modules[__name__] = _implementation
