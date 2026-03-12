"""Internationalization support for git-stage-batch.

This module provides translation functions using Python's gettext library:
- _() for translating strings
- ngettext() for translating plural forms
"""

from __future__ import annotations

import gettext
import importlib.resources
import locale

lang, _ = locale.getdefaultlocale()

translation = gettext.translation(
    "git-stage-batch",
    localedir=str(importlib.resources.files("git_stage_batch") / "locale"),
    languages=[lang],
    fallback=True,
)

translation.install()
_ = translation.gettext
ngettext = translation.ngettext
