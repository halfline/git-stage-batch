"""Compatibility import for batch ownership unit validation."""

import sys as _sys

from .ownership import unit_validation as _implementation


_sys.modules[__name__] = _implementation
