"""Compatibility import for batch merge presence placement choices."""

import sys as _sys

from .merge import presence_placement_choices as _implementation


_sys.modules[__name__] = _implementation
