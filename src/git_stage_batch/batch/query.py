"""Compatibility import for saved batch state query."""

import sys as _sys

from .state import query as _implementation


_sys.modules[__name__] = _implementation
