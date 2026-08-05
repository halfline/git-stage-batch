"""Tests for translated-message formatting."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import git_stage_batch.i18n as i18n


def test_translation_loader_honors_gettext_environment(monkeypatch) -> None:
    loaded = Mock(spec=i18n.gettext.NullTranslations)
    gettext_translation = Mock(return_value=loaded)
    monkeypatch.setattr(i18n.gettext, "translation", gettext_translation)

    assert i18n._load_translation() is loaded
    assert "languages" not in gettext_translation.call_args.kwargs


def test_rtl_locale_detection_handles_languages_and_script_subtags() -> None:
    assert i18n._locale_uses_rtl_writing_direction("ar_EG")
    assert i18n._locale_uses_rtl_writing_direction("ar_EG.UTF-8")
    assert i18n._locale_uses_rtl_writing_direction("he_IL@calendar=hebrew")
    assert i18n._locale_uses_rtl_writing_direction("az-Arab")
    assert not i18n._locale_uses_rtl_writing_direction("az-Latn")
    assert not i18n._locale_uses_rtl_writing_direction("ar-Latn")
    assert not i18n._locale_uses_rtl_writing_direction("ar_EG@latin")
    assert i18n._locale_uses_rtl_writing_direction("en_US@arabic")
    assert not i18n._locale_uses_rtl_writing_direction("en_US")


def test_ltr_message_formatting_does_not_add_directional_controls(
    monkeypatch,
) -> None:
    monkeypatch.setattr(i18n, "_USES_RTL_WRITING_DIRECTION", False)

    rendered = i18n._("File: {path}").format(path="src/example.py")

    assert rendered == "File: src/example.py"


def test_rtl_message_formatting_isolates_dynamic_values(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "_USES_RTL_WRITING_DIRECTION", True)

    rendered = i18n._("File: {path}; count: {count}").format(
        path="src/example.py",
        count=3,
    )

    assert rendered == ("File: \u2068src/example.py\u2069; count: \u20683\u2069")


def test_rtl_message_formatting_preserves_repr_conversion(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "_USES_RTL_WRITING_DIRECTION", True)

    rendered = i18n._("Invalid path: {path!r}").format(path="a b")

    assert rendered == "Invalid path: \u2068'a b'\u2069"


def test_rtl_message_formatting_preserves_standard_format_semantics(
    monkeypatch,
) -> None:
    monkeypatch.setattr(i18n, "_USES_RTL_WRITING_DIRECTION", True)

    rendered = i18n._(
        "{{value}} {0!r:>{width}} {item[name]} {1:.{precision}f}"
    ).format("x", 1.25, item={"name": "entry"}, width=4, precision=1)

    assert rendered == (
        "{value} \u2068 'x'\u2069 \u2068entry\u2069 \u20681.2\u2069"
    )

    automatic_item = i18n._("{[name]} {}").format(
        {"name": "entry"},
        "next",
    )
    assert automatic_item == "\u2068entry\u2069 \u2068next\u2069"


def test_rtl_message_formatting_preserves_numbering_errors(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "_USES_RTL_WRITING_DIRECTION", True)

    with pytest.raises(ValueError, match="manual field specification"):
        i18n._("{0} {}").format("one", "two")
    with pytest.raises(ValueError, match="automatic field numbering"):
        i18n._("{} {0}").format("one", "two")
    with pytest.raises(ValueError, match="manual field specification"):
        i18n._("{0[part]} {}").format({"part": "one"}, "two")
    with pytest.raises(ValueError, match="automatic field numbering"):
        i18n._("{} {0.real}").format(1, 2)
    with pytest.raises(ValueError, match="automatic field numbering"):
        i18n._("{} {2.real}").format(1)


def test_rtl_fragment_formatting_does_not_wrap_large_values() -> None:
    value = "content\n" * 100_000

    fragments, automatic_index = i18n._format_fragments(
        "{value}",
        (),
        {"value": value},
        isolate_fields=True,
        automatic_index=0,
        recursion_depth=2,
    )

    assert fragments[1] is value
    assert fragments == ["\u2068", value, "\u2069"]
    assert automatic_index == 0


def test_bidi_isolate_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "_USES_RTL_WRITING_DIRECTION", True)

    isolated = i18n.bidi_isolate("[i]")

    assert isolated == "\u2068[i]\u2069"
    assert i18n.bidi_isolate(isolated) == isolated


def test_bidi_isolation_fragments_reuse_large_values(monkeypatch) -> None:
    monkeypatch.setattr(i18n, "_USES_RTL_WRITING_DIRECTION", True)
    value = "content\n" * 100_000

    fragments = i18n.bidi_isolation_fragments(value)

    assert fragments[1] is value
    assert fragments == ("\u2068", value, "\u2069")
