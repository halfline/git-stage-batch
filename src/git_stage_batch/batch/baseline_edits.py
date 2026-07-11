"""Compatibility import for batch merge baseline edits."""

import sys as _sys

from .merge import baseline_edits as _implementation


_sys.modules[__name__] = _implementation
