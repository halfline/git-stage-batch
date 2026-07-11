"""Compatibility import for batch ownership translation."""

import sys as _sys

from .ownership import translation as _implementation


_sys.modules[__name__] = _implementation
