"""Compatibility import for batch ownership replacement units."""

import sys as _sys

from .ownership import replacement_units as _implementation


_sys.modules[__name__] = _implementation
