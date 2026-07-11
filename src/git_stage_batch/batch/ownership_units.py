"""Compatibility import for batch ownership units."""

import sys as _sys

from .ownership import units as _implementation


_sys.modules[__name__] = _implementation
