"""Compatibility import for realized-entry mapping."""

import sys as _sys

from .realization import mapping as _implementation


_sys.modules[__name__] = _implementation
