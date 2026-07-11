"""Compatibility import for batch merge baseline reference positions."""

import sys as _sys

from .merge import baseline_reference_positions as _implementation


_sys.modules[__name__] = _implementation
