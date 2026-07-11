"""Compatibility import for batch ownership acquisition."""

import sys as _sys

from .ownership import acquisition as _implementation


_sys.modules[__name__] = _implementation
