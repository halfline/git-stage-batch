"""Compatibility import for batch merge validation."""

import sys as _sys

from .merge import validation as _implementation


_sys.modules[__name__] = _implementation
