"""Compatibility import for saved batch state reference names."""

import sys as _sys

from .state import reference_names as _implementation


_sys.modules[__name__] = _implementation
