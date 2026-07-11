"""Compatibility import for realized-entry boundary lookup."""

import sys as _sys

from .realization import boundaries as _implementation


_sys.modules[__name__] = _implementation
