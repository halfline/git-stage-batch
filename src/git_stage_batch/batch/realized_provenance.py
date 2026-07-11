"""Compatibility import for realized-entry provenance."""

import sys as _sys

from .realization import provenance as _implementation


_sys.modules[__name__] = _implementation
