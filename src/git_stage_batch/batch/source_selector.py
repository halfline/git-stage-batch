"""Temporary compatibility import for batch source selectors."""

import sys as _sys

from .source import selector as _implementation

_sys.modules[__name__] = _implementation
