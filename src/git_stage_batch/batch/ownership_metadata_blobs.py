"""Compatibility import for batch ownership metadata blobs."""

import sys as _sys

from .ownership import metadata_blobs as _implementation


_sys.modules[__name__] = _implementation
