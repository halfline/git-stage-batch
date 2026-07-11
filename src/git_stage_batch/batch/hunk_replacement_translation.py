"""Compatibility import for batch ownership hunk replacement translation."""

import sys as _sys

from .ownership import hunk_replacement_translation as _implementation


_sys.modules[__name__] = _implementation
