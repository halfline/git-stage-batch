"""Internationalization support for git-stage-batch."""

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
