"""Compatibility import for batch ownership unit types."""

import sys as _sys

from .ownership import unit_types as _implementation


_sys.modules[__name__] = _implementation
