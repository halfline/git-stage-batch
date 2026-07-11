"""Compatibility import for saved batch state metadata schema."""

import sys as _sys

from .state import metadata_schema as _implementation


_sys.modules[__name__] = _implementation
