"""Compatibility import for saved batch state validation."""

import sys as _sys

from .state import validation as _implementation


_sys.modules[__name__] = _implementation
