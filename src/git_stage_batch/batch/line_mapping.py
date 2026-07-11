"""Compatibility import for line mappings."""

import sys as _sys

from .line_matching import line_mapping as _implementation


_sys.modules[__name__] = _implementation
