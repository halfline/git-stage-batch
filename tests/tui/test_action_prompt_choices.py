"""Tests for interactive action prompt choice normalization."""

from git_stage_batch.tui.action_prompt_choices import normalize_action_prompt_choice


def test_case_sensitive_single_letter_actions_are_preserved():
    """Documented uppercase actions must not collapse into lowercase actions."""
    assert normalize_action_prompt_choice("U") == "U"
    assert normalize_action_prompt_choice("S") == "S"
    assert normalize_action_prompt_choice("A") == "A"


def test_word_aliases_remain_case_insensitive():
    """Full action names should continue to accept mixed case."""
    assert normalize_action_prompt_choice("Redo") == "U"
    assert normalize_action_prompt_choice("STATUS") == "S"
    assert normalize_action_prompt_choice("Assets") == "A"
