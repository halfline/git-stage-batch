"""Compatibility import for saved batch state compatibility metadata."""

import sys as _sys

from .state import compatibility_metadata as _implementation


_sys.modules[__name__] = _implementation
