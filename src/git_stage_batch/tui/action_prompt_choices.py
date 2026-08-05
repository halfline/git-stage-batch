"""Action prompt choices and normalization for interactive TUI mode."""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from functools import cache

from ..i18n import _
from ..output.colors import Colors


ActionPromptOption = tuple[str, str, str]
_MAX_LOCALIZED_CHOICE_CHARACTERS = 256
_LOCALIZABLE_ACTION_WORDS = (
    ("include", "i"),
    ("skip", "s"),
    ("discard", "d"),
    ("quit", "q"),
    ("again", "a"),
    ("undo", "u"),
    ("redo", "U"),
    ("status", "S"),
    ("assets", "A"),
    ("lines", "l"),
    ("file", "f"),
    ("view", "v"),
    ("open", "o"),
    ("batch", "b"),
    ("fixup", "x"),
    ("command", "!"),
    ("help", "?"),
    ("from", "<"),
    ("to", ">"),
)
_STABLE_ACTION_CODES = frozenset(
    {
        "i",
        "s",
        "d",
        "q",
        "a",
        "u",
        "U",
        "S",
        "A",
        "l",
        "f",
        "v",
        "o",
        "b",
        "x",
        "!",
        "?",
        "<",
        ">",
    }
)
_LEGACY_ACTION_WORD_TO_LETTER = {
    "include": "i",
    "skip": "s",
    "discard": "d",
    "quit": "q",
    "again": "a",
    "undo": "u",
    "redo": "U",
    "status": "S",
    "assets": "A",
    "install-assets": "A",
    "lines": "l",
    "file": "f",
    "review": "v",
    "view": "v",
    "open": "o",
    "files": "o",
    "batch": "b",
    "fixup": "x",
    "cmd": "!",
    "command": "!",
    "help": "?",
    "from": "<",
    "to": ">",
}


@dataclass(frozen=True)
class ActionPromptOptionGroups:
    primary: tuple[ActionPromptOption, ...]
    scope: tuple[ActionPromptOption, ...]
    flow: tuple[ActionPromptOption, ...]
    more: tuple[ActionPromptOption, ...]


@cache
def _localized_action_word_to_letter() -> dict[str, str]:
    """Return translated action labels mapped to their stable action codes."""
    return localized_word_aliases(
        (str(_(word)), action) for word, action in _LOCALIZABLE_ACTION_WORDS
    )


def localized_word_aliases(
    words_and_actions: Iterable[tuple[str, str]],
) -> dict[str, str]:
    """Return unambiguous exact and optional-diacritic word aliases."""
    exact_candidates: dict[str, set[str]] = {}
    unmarked_candidates: dict[str, set[str]] = {}
    for word, action in words_and_actions:
        translated = _action_word_key(word)
        exact_candidates.setdefault(translated, set()).add(action)
        unmarked = _action_word_without_marks(translated)
        if unmarked != translated:
            unmarked_candidates.setdefault(unmarked, set()).add(action)

    result = {
        word: next(iter(actions))
        for word, actions in exact_candidates.items()
        if len(actions) == 1
    }
    result.update(
        {
            word: next(iter(actions))
            for word, actions in unmarked_candidates.items()
            if len(actions) == 1 and word not in exact_candidates
        }
    )
    return result


def normalize_localized_choice(
    choice: str,
    *,
    stable_codes: Set[str],
    legacy_words: Mapping[str, str],
    localized_words: Mapping[str, str],
) -> str:
    """Normalize stable codes and unambiguous legacy or localized words."""
    if len(choice) > _MAX_LOCALIZED_CHOICE_CHARACTERS:
        return choice
    if choice in stable_codes:
        return choice

    choice_key = _action_word_key(choice)
    legacy_action = legacy_words.get(choice_key)
    if legacy_action is not None:
        return legacy_action

    return localized_words.get(
        choice_key,
        localized_words.get(
            _action_word_without_marks(choice_key),
            choice_key,
        ),
    )


def _action_word_key(word: str) -> str:
    """Return the canonical exact-match key for one action word."""
    return unicodedata.normalize("NFC", word).casefold()


def _action_word_without_marks(word: str) -> str:
    """Return an optional-diacritic alias for one action word."""
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", word)
        if not unicodedata.combining(character)
    )


def action_prompt_option_groups(
    *,
    has_hunk: bool,
    use_color: bool,
) -> ActionPromptOptionGroups:
    """Return grouped action choices for the interactive action prompt."""
    if has_hunk:
        return ActionPromptOptionGroups(
            primary=(
                (_("include"), "i", Colors.GREEN if use_color else ""),
                (_("skip"), "s", ""),
                (_("discard"), "d", Colors.RED if use_color else ""),
                (_("quit"), "q", ""),
            ),
            scope=(
                (_("lines"), "l", ""),
                (_("file"), "f", ""),
                (_("view"), "v", ""),
            ),
            flow=(
                (_("from"), "<", ""),
                (_("to"), ">", ""),
            ),
            more=(
                (_("again"), "a", ""),
                (_("undo"), "u", ""),
                (_("redo"), "U", ""),
                (_("status"), "S", ""),
                (_("assets"), "A", ""),
                (_("batch"), "b", ""),
                (_("open"), "o", ""),
                (_("fixup"), "x", ""),
                (_("command"), "!", ""),
                (_("help"), "?", ""),
            ),
        )

    return ActionPromptOptionGroups(
        primary=(
            (_("quit"), "q", ""),
            (_("help"), "?", ""),
        ),
        scope=(),
        flow=(
            (_("from"), "<", ""),
            (_("to"), ">", ""),
        ),
        more=(
            (_("undo"), "u", ""),
            (_("redo"), "U", ""),
            (_("status"), "S", ""),
            (_("assets"), "A", ""),
            (_("batch"), "b", ""),
            (_("open"), "o", ""),
            (_("command"), "!", ""),
        ),
    )


def normalize_action_prompt_choice(choice: str) -> str:
    """Return the single-character action code for a prompt choice."""
    return normalize_localized_choice(
        choice,
        stable_codes=_STABLE_ACTION_CODES,
        legacy_words=_LEGACY_ACTION_WORD_TO_LETTER,
        localized_words=_localized_action_word_to_letter(),
    )


def normalize_change_action_choice(choice: str) -> str:
    """Normalize an include, skip, or discard submenu choice."""
    return normalize_localized_choice(
        choice,
        stable_codes=frozenset({"i", "s", "d"}),
        legacy_words={
            "include": "i",
            "skip": "s",
            "discard": "d",
        },
        localized_words=localized_word_aliases(
            (
                (str(_("include")), "i"),
                (str(_("skip")), "s"),
                (str(_("discard")), "d"),
            )
        ),
    )
