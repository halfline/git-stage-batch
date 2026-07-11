"""Temporary compatibility import for selected-line source refresh."""

import sys as _sys

from .source import selected_line_refresh as _implementation

_sys.modules[__name__] = _implementation
