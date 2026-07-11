"""Compatibility import for batch ownership unit rebuild."""

import sys as _sys

from .ownership import unit_rebuild as _implementation


_sys.modules[__name__] = _implementation
