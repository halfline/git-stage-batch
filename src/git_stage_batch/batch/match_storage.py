"""Compatibility import for line-matching workspace storage."""

import sys as _sys

from .line_matching import match_workspace as _implementation


_sys.modules[__name__] = _implementation
