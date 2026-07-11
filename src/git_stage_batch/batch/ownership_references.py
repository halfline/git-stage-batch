"""Compatibility import for batch ownership references."""

import sys as _sys

from .ownership import references as _implementation


_sys.modules[__name__] = _implementation
