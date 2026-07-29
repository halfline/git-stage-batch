"""Replacement text input handling for CLI commands."""

from __future__ import annotations

import argparse
import sys

from ..core.replacement import ReplacementText
from ..exceptions import CommandError
from ..i18n import _


def resolve_replacement_text(args: argparse.Namespace) -> ReplacementText | None:
    """Return replacement text from `--as` or exact stdin content."""
    if getattr(args, "as_text", None) is not None and getattr(args, "as_stdin", False):
        raise CommandError(_("Cannot use `--as` and `--as-stdin` together."))
    if getattr(args, "as_stdin", False):
        data = sys.stdin.buffer.read()
        return ReplacementText(
            data.decode("utf-8", errors="surrogateescape"),
            data=data,
            exact=True,
        )
    as_text = getattr(args, "as_text", None)
    if as_text is not None:
        return ReplacementText(as_text, exact=True)
    return None


def require_replacement_text(args: argparse.Namespace) -> ReplacementText:
    """Return requested replacement text or reject an inconsistent dispatch."""
    replacement_text = resolve_replacement_text(args)
    if replacement_text is None:
        raise CommandError(_("Replacement text was not provided."))
    return replacement_text
