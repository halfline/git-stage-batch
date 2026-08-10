"""Scratch-directory selection for potentially large disposable files."""

from __future__ import annotations

import os
from pathlib import Path
import sys


_SCRATCH_ENVIRONMENT_VARIABLES = ("TMPDIR", "TEMP", "TMP")


def default_scratch_parent() -> Path | None:
    """Return the current process's preferred parent for large scratch data."""
    for variable in _SCRATCH_ENVIRONMENT_VARIABLES:
        configured = os.environ.get(variable)
        if configured:
            return Path(configured)
    if sys.platform == "linux":
        return Path("/var/tmp")
    return None
