"""Compatibility import for batch ownership metadata loading."""

import sys as _sys

from .ownership import metadata_loading as _implementation


_sys.modules[__name__] = _implementation
