"""Compatibility import for batch ownership absence content."""

import sys as _sys

from .ownership import absence_content as _implementation


_sys.modules[__name__] = _implementation
