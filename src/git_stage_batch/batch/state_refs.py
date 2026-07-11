"""Compatibility import for saved batch state references."""

import sys as _sys

from .state import references as _implementation


_sys.modules[__name__] = _implementation
