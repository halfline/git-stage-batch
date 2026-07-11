"""Compatibility import for batch ownership absence claims."""

import sys as _sys

from .ownership import absence_claims as _implementation


_sys.modules[__name__] = _implementation
