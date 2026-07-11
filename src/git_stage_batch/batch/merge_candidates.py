"""Compatibility import for batch merge candidates."""

import sys as _sys

from .merge import candidates as _implementation


_sys.modules[__name__] = _implementation
