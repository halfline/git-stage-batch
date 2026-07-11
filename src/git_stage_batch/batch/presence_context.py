"""Compatibility import for batch merge presence context."""

import sys as _sys

from .merge import presence_context as _implementation


_sys.modules[__name__] = _implementation
