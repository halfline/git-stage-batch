"""Compatibility import for batch-source lineage."""

import sys as _sys

from .line_matching import lineage as _implementation


_sys.modules[__name__] = _implementation
