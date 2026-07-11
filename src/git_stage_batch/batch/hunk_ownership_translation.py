"""Compatibility import for batch ownership hunk translation."""

import sys as _sys

from .ownership import hunk_translation as _implementation


_sys.modules[__name__] = _implementation
