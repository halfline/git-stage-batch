"""Tests for interactive action prompt choice normalization."""

from git_stage_batch.tui import action_prompt_choices
from git_stage_batch.tui.action_prompt_choices import (
    normalize_action_prompt_choice,
    normalize_change_action_choice,
)


def test_case_sensitive_single_letter_actions_are_preserved():
    """Documented uppercase actions must not collapse into lowercase actions."""
    assert normalize_action_prompt_choice("U") == "U"
    assert normalize_action_prompt_choice("S") == "S"
    assert normalize_action_prompt_choice("A") == "A"


def test_change_submenu_letter_actions_remain_case_insensitive():
    """Submenus without uppercase meanings retain their historical behavior."""
    assert normalize_change_action_choice("I") == "i"
    assert normalize_change_action_choice("S") == "s"
    assert normalize_change_action_choice("D") == "d"


def test_change_submenu_accepts_localized_action_words(monkeypatch):
    translations = {
        "include": "ضمّ",
        "skip": "تخطّي",
        "discard": "نبذ",
    }
    monkeypatch.setattr(
        action_prompt_choices,
        "_",
        lambda message: translations.get(message, message),
    )

    assert normalize_change_action_choice("ضمّ") == "i"
    assert normalize_change_action_choice("تخطي") == "s"
    assert normalize_change_action_choice("نبذ") == "d"


def test_localized_words_cannot_override_stable_action_codes(monkeypatch):
    """Typing a displayed hotkey must always select that hotkey's action."""
    monkeypatch.setattr(
        action_prompt_choices,
        "_localized_action_word_to_letter",
        lambda: {"i": "s"},
    )

    assert normalize_action_prompt_choice("i") == "i"


def test_word_aliases_remain_case_insensitive():
    """Full action names should continue to accept mixed case."""
    assert normalize_action_prompt_choice("Redo") == "U"
    assert normalize_action_prompt_choice("STATUS") == "S"
    assert normalize_action_prompt_choice("Assets") == "A"


def test_legacy_command_abbreviation_remains_accepted():
    """The formerly displayed ``cmd`` label remains a valid action alias."""
    assert normalize_action_prompt_choice("cmd") == "!"


def test_localized_words_cannot_override_legacy_word_aliases(monkeypatch):
    """Ambiguous translated words preserve the established English action."""
    monkeypatch.setattr(
        action_prompt_choices,
        "_localized_action_word_to_letter",
        lambda: {"skip": "i"},
    )

    assert normalize_action_prompt_choice("skip") == "s"


def test_oversized_action_input_skips_unicode_normalization() -> None:
    choice = "X" * 100_000

    assert normalize_action_prompt_choice(choice) is choice


def test_every_visible_action_choice_is_translated(monkeypatch):
    """Interactive action labels must all pass through gettext."""
    monkeypatch.setattr(
        action_prompt_choices,
        "_",
        lambda message: f"translated:{message}",
    )

    groups = action_prompt_choices.action_prompt_option_groups(
        has_hunk=True,
        use_color=False,
    )

    assert all(
        label.startswith("translated:")
        for group in (groups.primary, groups.scope, groups.flow, groups.more)
        for label, _hotkey, _color in group
    )


def test_localized_action_words_are_accepted(monkeypatch):
    """Typing a translated label should select the action it describes."""
    monkeypatch.setattr(
        action_prompt_choices,
        "_localized_action_word_to_letter",
        lambda: {"ضمّ": "i", "تجاهل": "d"},
    )

    assert normalize_action_prompt_choice("ضمّ") == "i"
    assert normalize_action_prompt_choice("تجاهل") == "d"


def test_localized_action_words_accept_omitted_diacritics(monkeypatch):
    """Arabic action words remain usable without optional vowel marks."""
    translations = {
        word: "ضمّ" if word == "include" else word
        for word, _action in action_prompt_choices._LOCALIZABLE_ACTION_WORDS
    }
    monkeypatch.setattr(
        action_prompt_choices,
        "_",
        lambda message: translations[message],
    )
    action_prompt_choices._localized_action_word_to_letter.cache_clear()
    try:
        assert normalize_action_prompt_choice("ضم") == "i"
    finally:
        action_prompt_choices._localized_action_word_to_letter.cache_clear()


def test_diacritic_alias_does_not_override_an_exact_localized_word(monkeypatch):
    """An accent-free alias must not steal another action's exact spelling."""
    translations = {
        word: {"include": "é", "skip": "e"}.get(word, word)
        for word, _action in action_prompt_choices._LOCALIZABLE_ACTION_WORDS
    }
    monkeypatch.setattr(
        action_prompt_choices,
        "_",
        lambda message: translations[message],
    )
    action_prompt_choices._localized_action_word_to_letter.cache_clear()
    try:
        assert normalize_action_prompt_choice("é") == "i"
        assert normalize_action_prompt_choice("e") == "s"
    finally:
        action_prompt_choices._localized_action_word_to_letter.cache_clear()
