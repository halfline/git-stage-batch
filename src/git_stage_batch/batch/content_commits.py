"""Compatibility import for saved batch state content commits."""

import sys as _sys

from .state import content_commits as _implementation


_sys.modules[__name__] = _implementation
