"""Compatibility import for realized-entry storage."""

import sys as _sys

from .realization import entry_storage as _implementation


_sys.modules[__name__] = _implementation
