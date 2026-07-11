"""Compatibility import for batch merge baseline replacement choices."""

import sys as _sys

from .merge import baseline_replacement_choices as _implementation


_sys.modules[__name__] = _implementation
