"""Compatibility import for saved batch state batch names."""

import sys as _sys

from .state import batch_names as _implementation


_sys.modules[__name__] = _implementation
