"""Compatibility import for batch merge candidate enumeration."""

import sys as _sys

from .merge import candidate_enumeration as _implementation


_sys.modules[__name__] = _implementation
