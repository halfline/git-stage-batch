"""Compatibility import for batch merge presence constraints."""

import sys as _sys

from .merge import presence_constraints as _implementation


_sys.modules[__name__] = _implementation
