"""Compatibility import for batch ownership claims."""

import sys as _sys

from .ownership import claims as _implementation


_sys.modules[__name__] = _implementation
