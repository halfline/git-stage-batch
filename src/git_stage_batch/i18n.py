"""Internationalization support for git-stage-batch.

This module provides translation functions using Python's gettext library:
- _() for translating strings
- ngettext() for translating plural forms
- pgettext() for translating strings with context
"""

from __future__ import annotations

import gettext
import importlib.resources
import locale

# Get language from locale (replacement for deprecated getdefaultlocale)
try:
    locale.setlocale(locale.LC_MESSAGES, '')
    lang, encoding = locale.getlocale(locale.LC_MESSAGES)
except (locale.Error, ValueError):
    lang = None

translation = gettext.translation(
    "git-stage-batch",
    localedir=str(importlib.resources.files("git_stage_batch") / "locale"),
    languages=[lang] if lang else None,
    fallback=True,
)

translation.install()
_ = translation.gettext
ngettext = translation.ngettext
pgettext = translation.pgettext
