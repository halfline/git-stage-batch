"""Internationalization support for git-stage-batch.

This module provides translation functions using Python's gettext library:
- _() for translating strings
- ngettext() for translating plural forms
- pgettext() for translating strings with context
- npgettext() for contextual plural forms
"""

from __future__ import annotations

import gettext
import importlib.resources
import locale
import string


_FIRST_STRONG_ISOLATE = "\u2068"
_POP_DIRECTIONAL_ISOLATE = "\u2069"
_RTL_LANGUAGES = frozenset(
    {"ar", "arc", "ckb", "dv", "fa", "he", "ks", "ps", "sd", "ug", "ur", "yi"}
)
_RTL_SCRIPTS = frozenset({"adlm", "arab", "hebr", "nkoo", "rohg", "syrc", "thaa"})
_SCRIPT_MODIFIERS = {
    "arabic": "arab",
    "hebrew": "hebr",
    "latin": "latn",
}

# Get language from locale (replacement for deprecated getdefaultlocale)
try:
    locale.setlocale(locale.LC_MESSAGES, '')
    lang, encoding = locale.getlocale(locale.LC_MESSAGES)
except (locale.Error, ValueError):
    lang = None


def _load_translation() -> gettext.NullTranslations:
    """Load translations using gettext's standard locale environment rules."""
    return gettext.translation(
        "git-stage-batch",
        localedir=str(importlib.resources.files("git_stage_batch") / "locale"),
        fallback=True,
    )


translation = _load_translation()

translation.install()


def _locale_uses_rtl_writing_direction(language: object) -> bool:
    """Return whether a locale tag conventionally uses an RTL script."""
    locale_with_modifier = str(language).split(".", 1)[0]
    locale_name, _separator, modifier = locale_with_modifier.partition("@")
    parts = locale_name.replace("_", "-").lower().split("-")
    explicit_script = next(
        (
            part
            for part in parts[1:]
            if len(part) == 4 and part.isalpha()
        ),
        None,
    )
    if explicit_script is not None:
        return explicit_script in _RTL_SCRIPTS

    modifier_script = _SCRIPT_MODIFIERS.get(modifier.lower(), modifier.lower())
    if modifier_script in _RTL_SCRIPTS or modifier_script == "latn":
        return modifier_script in _RTL_SCRIPTS

    return bool(parts and parts[0] in _RTL_LANGUAGES)


_USES_RTL_WRITING_DIRECTION = _locale_uses_rtl_writing_direction(
    translation.info().get("language") or lang or ""
)


_FORMATTER = string.Formatter()


def _format_field_first_component(field_name: str) -> str:
    """Return the argument-name portion before attribute or item traversal."""
    end = len(field_name)
    for separator in (".", "["):
        separator_index = field_name.find(separator)
        if separator_index >= 0:
            end = min(end, separator_index)
    return field_name[:end]


def _format_fragments(
    template: str,
    args: tuple[object, ...],
    kwargs: dict[str, object],
    *,
    isolate_fields: bool,
    automatic_index: int | None,
    recursion_depth: int,
) -> tuple[list[str], int | None]:
    """Format into fragments while preserving standard field-numbering rules."""
    if recursion_depth < 0:
        raise ValueError("Max string recursion exceeded")

    fragments: list[str] = []
    for literal, field_name, format_spec, conversion in _FORMATTER.parse(template):
        if literal:
            fragments.append(literal)
        if field_name is None:
            continue

        field_first = _format_field_first_component(field_name)
        uses_automatic_field = field_first == ""
        if uses_automatic_field:
            if automatic_index is None:
                raise ValueError(
                    "cannot switch from manual field specification to automatic "
                    "field numbering"
                )
            field_name = str(automatic_index) + field_name
            automatic_index += 1
        elif field_first.isdecimal():
            if automatic_index not in (0, None):
                raise ValueError(
                    "cannot switch from automatic field numbering to manual "
                    "field specification"
                )
            automatic_index = None
        value, _used_key = _FORMATTER.get_field(field_name, args, kwargs)
        value = _FORMATTER.convert_field(value, conversion)
        format_fragments, automatic_index = _format_fragments(
            format_spec or "",
            args,
            kwargs,
            isolate_fields=False,
            automatic_index=automatic_index,
            recursion_depth=recursion_depth - 1,
        )
        rendered = _FORMATTER.format_field(value, "".join(format_fragments))
        if isolate_fields:
            # Keep controls separate so a large dynamic string is not copied into
            # an intermediate wrapper before the final result is joined.
            fragments.extend(
                (_FIRST_STRONG_ISOLATE, rendered, _POP_DIRECTIONAL_ISOLATE)
            )
        else:
            fragments.append(rendered)
    return fragments, automatic_index


class _TranslatedString(str):
    """Translated text that isolates dynamic fields for RTL display."""

    def format(self, *args: object, **kwargs: object) -> str:
        if not _USES_RTL_WRITING_DIRECTION:
            return super().format(*args, **kwargs)
        fragments, _automatic_index = _format_fragments(
            self,
            args,
            kwargs,
            isolate_fields=True,
            automatic_index=0,
            recursion_depth=2,
        )
        return "".join(fragments)


def _translated(message: str) -> _TranslatedString:
    return _TranslatedString(message)


def bidi_isolate(value: object) -> str:
    """Protect one dynamic display run when the active locale is RTL."""
    fragments = bidi_isolation_fragments(value)
    if len(fragments) == 1:
        return fragments[0]
    return "".join(fragments)


def bidi_isolation_fragments(value: object) -> tuple[str, ...]:
    """Return constant-count fragments that isolate one dynamic RTL run."""
    rendered = str(value)
    if (
        not _USES_RTL_WRITING_DIRECTION
        or rendered.startswith(_FIRST_STRONG_ISOLATE)
        and rendered.endswith(_POP_DIRECTIONAL_ISOLATE)
    ):
        return (rendered,)
    return (_FIRST_STRONG_ISOLATE, rendered, _POP_DIRECTIONAL_ISOLATE)


def _(message: str) -> _TranslatedString:
    """Translate one message and protect RTL interpolation boundaries."""
    return _translated(translation.gettext(message))


def ngettext(singular: str, plural: str, count: int) -> _TranslatedString:
    """Translate a cardinal message using the active locale's plural rules."""
    return _translated(translation.ngettext(singular, plural, count))


def pgettext(context: str, message: str) -> _TranslatedString:
    """Translate one message disambiguated by translator context."""
    return _translated(translation.pgettext(context, message))


def npgettext(
    context: str,
    singular: str,
    plural: str,
    count: int,
) -> _TranslatedString:
    """Translate a cardinal message disambiguated by translator context."""
    return _translated(translation.npgettext(context, singular, plural, count))
