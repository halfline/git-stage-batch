"""Compatibility import for batch merge absence constraints."""

import sys as _sys

from .merge import absence_constraints as _implementation


_sys.modules[__name__] = _implementation
