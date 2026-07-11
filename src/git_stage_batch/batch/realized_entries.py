"""Compatibility import for realized entry views."""

import sys as _sys

from .realization import entries as _implementation


_sys.modules[__name__] = _implementation
