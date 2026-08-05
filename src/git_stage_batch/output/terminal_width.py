"""Terminal-cell width helpers for translated display text."""

from __future__ import annotations

import unicodedata


_ZERO_WIDTH_FORMAT_CHARACTERS = frozenset(
    {
        "\u200c",  # ZERO WIDTH NON-JOINER
        "\u200d",  # ZERO WIDTH JOINER
        "\u2066",  # LEFT-TO-RIGHT ISOLATE
        "\u2067",  # RIGHT-TO-LEFT ISOLATE
        "\u2068",  # FIRST STRONG ISOLATE
        "\u2069",  # POP DIRECTIONAL ISOLATE
    }
)
_EMOJI_MODIFIER_RANGE = range(0x1F3FB, 0x1F400)
_EMOJI_PRESENTATION_SELECTOR = "\ufe0f"


def _is_probable_emoji(character: str) -> bool:
    """Return whether a character commonly participates in emoji clusters."""
    codepoint = ord(character)
    return (
        0x1F000 <= codepoint <= 0x1FAFF
        or 0x2600 <= codepoint <= 0x27BF
        or codepoint in {0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139}
    )


def terminal_cell_width(text: str) -> int:
    """Return an approximate terminal-cell width for Unicode text."""
    width = 0
    cluster_width = 0
    cluster_is_emoji = False
    join_next = False
    for character in text:
        if character == "\u200d":
            join_next = cluster_is_emoji
            continue
        if character == _EMOJI_PRESENTATION_SELECTOR:
            if cluster_width == 1:
                width += 1
                cluster_width = 2
            cluster_is_emoji = True
            continue
        if ord(character) in _EMOJI_MODIFIER_RANGE:
            cluster_is_emoji = True
            continue
        if character in _ZERO_WIDTH_FORMAT_CHARACTERS:
            continue
        if unicodedata.combining(character):
            continue
        category = unicodedata.category(character)
        if category in {"Cc", "Cf", "Mc", "Me", "Mn"}:
            continue
        character_width = (
            2 if unicodedata.east_asian_width(character) in {"F", "W"} else 1
        )
        character_is_emoji = _is_probable_emoji(character)
        if join_next and character_is_emoji:
            if character_width > cluster_width:
                width += character_width - cluster_width
            cluster_width = max(cluster_width, character_width)
        else:
            width += character_width
            cluster_width = character_width
        cluster_is_emoji = character_is_emoji
        join_next = False
    return width


def pad_to_terminal_width(text: str, width: int) -> str:
    """Right-pad text until it occupies at least ``width`` terminal cells."""
    return text + " " * max(0, width - terminal_cell_width(text))
