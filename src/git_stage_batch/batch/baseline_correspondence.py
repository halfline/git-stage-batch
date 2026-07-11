"""Compatibility import for batch merge baseline correspondence."""

import sys as _sys

from .merge import baseline_correspondence as _implementation


_sys.modules[__name__] = _implementation
