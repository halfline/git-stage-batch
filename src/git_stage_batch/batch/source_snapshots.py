"""Temporary compatibility import for batch source snapshots."""

import sys as _sys

from .source import snapshots as _implementation

_sys.modules[__name__] = _implementation
