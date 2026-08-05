"""Prompt text and action parsing for file review."""

from __future__ import annotations

from functools import cache

from ...i18n import _
from ..action_prompt_choices import (
    localized_word_aliases,
    normalize_localized_choice,
)
from ..flow import FlowState, LocationRole
from ..prompts import unlocked_input, wrap_prompt_for_readline


_STABLE_REVIEW_ACTIONS = frozenset(
    {
        "i",
        "s",
        "d",
        "r",
        "I",
        "S",
        "D",
        "B",
        "U",
        "x",
        "c",
        "n",
        "p",
        "g",
        "o",
        "q",
        "?",
    }
)
_LEGACY_REVIEW_WORDS = {
    "include": "i",
    "skip": "s",
    "discard": "d",
    "replace": "r",
    "include-file": "I",
    "include file": "I",
    "skip-file": "S",
    "skip file": "S",
    "discard-file": "D",
    "discard file": "D",
    "block": "B",
    "block-file": "B",
    "block file": "B",
    "unblock": "U",
    "unblock-file": "U",
    "unblock file": "U",
    "fixup": "x",
    "fixup-lines": "x",
    "fixup lines": "x",
    "candidates": "c",
    "candidate": "c",
    "next": "n",
    "prev": "p",
    "previous": "p",
    "page": "g",
    "goto": "g",
    "open": "o",
    "files": "o",
    "back": "q",
    "quit": "q",
    "help": "?",
}


@cache
def _localized_review_words() -> dict[str, str]:
    """Return translated file-review labels mapped to stable actions."""
    return localized_word_aliases(
        (
            (str(_("include")), "i"),
            (str(_("skip")), "s"),
            (str(_("discard")), "d"),
            (str(_("replace")), "r"),
            (str(_("include file")), "I"),
            (str(_("skip file")), "S"),
            (str(_("discard file")), "D"),
            (str(_("block")), "B"),
            (str(_("unblock")), "U"),
            (str(_("fixup")), "x"),
            (str(_("candidates")), "c"),
            (str(_("candidate")), "c"),
            (str(_("next")), "n"),
            (str(_("previous")), "p"),
            (str(_("page")), "g"),
            (str(_("open")), "o"),
            (str(_("back")), "q"),
            (str(_("quit")), "q"),
            (str(_("help")), "?"),
        )
    )


def prompt_review_action(flow_state: FlowState) -> str:
    """Prompt for the next reviewed-file action."""
    print()
    if flow_state.source.role is LocationRole.BATCH:
        print(
            _(
                "Review action: [i]nclude lines [d]iscard lines "
                "[r]eplace lines [I]include file [D]discard file "
                "[B]block [U]unblock [c]andidates [n]next [p]prev [g]page "
                "[o]open [q]back [?]help"
            )
        )
    else:
        print(
            _(
                "Review action: [i]nclude lines [s]kip lines [d]iscard lines "
                "[r]eplace lines [I]include file [S]skip file [D]discard file "
                "[B]block [U]unblock [x]fixup lines [n]next [p]prev [g]page "
                "[o]open [q]back [?]help"
            )
        )

    try:
        return unlocked_input(wrap_prompt_for_readline(_("Action: "))).strip()
    except (KeyboardInterrupt, EOFError):
        return "q"


def normalize_review_action(action: str) -> str:
    """Return the canonical action key for a file-review action."""
    return normalize_localized_choice(
        action,
        stable_codes=_STABLE_REVIEW_ACTIONS,
        legacy_words=_LEGACY_REVIEW_WORDS,
        localized_words=_localized_review_words(),
    )


def print_review_help(flow_state: FlowState) -> None:
    """Print file-review help text for the current source."""
    print()
    print(_("File Review Commands:"))
    print(_("  i, include       Include selected file-review line IDs"))
    if flow_state.source.role is not LocationRole.BATCH:
        print(_("  s, skip          Skip selected file-review line IDs"))
    print(_("  d, discard       Discard selected file-review line IDs"))
    print(_("  r, replace       Replace selected line IDs through current flow"))
    if flow_state.source.role is not LocationRole.BATCH:
        print(_("  x, fixup         Suggest fixup commits for selected line IDs"))
    if flow_state.source.role is LocationRole.BATCH:
        print(_("  c, candidates    Preview or execute batch candidates"))
    print(_("  I                Include the reviewed file"))
    if flow_state.source.role is not LocationRole.BATCH:
        print(_("  S                Skip the reviewed file"))
    print(_("  D                Discard the reviewed file"))
    print(_("  B                Block the reviewed file"))
    print(_("  U                Unblock the reviewed file"))
    print(_("  n, next          Show the next file review page"))
    print(_("  p, prev          Show the previous file review page"))
    print(_("  g, page          Show a page or page range"))
    print(_("  o, open          Choose another reviewable file"))
    print(_("  q, back          Return to hunk review"))
