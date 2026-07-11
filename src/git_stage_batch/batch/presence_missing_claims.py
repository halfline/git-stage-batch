"""Compatibility import for batch merge presence missing claims."""

import sys as _sys

from .merge import presence_missing_claims as _implementation


_sys.modules[__name__] = _implementation
