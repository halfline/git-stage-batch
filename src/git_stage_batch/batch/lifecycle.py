"""Compatibility import for saved batch state lifecycle."""

import sys as _sys

from .state import lifecycle as _implementation


_sys.modules[__name__] = _implementation
