#!/usr/bin/env python3
"""Verify that gettext catalogs are synchronized and compilable."""

from __future__ import annotations

import ast
import gettext
import re
import shutil
import string
import subprocess
import sys
import tempfile
import unicodedata
from collections import Counter
from pathlib import Path

from generate_potfiles import find_translatable_files


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PO_DIRECTORY = PROJECT_ROOT / "po"
POTFILES_PATH = PO_DIRECTORY / "POTFILES.in"
_DIRECTIONAL_ISOLATE_STARTS = frozenset({"\u2066", "\u2067", "\u2068"})
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
_REQUIRED_COMPLETE_LANGUAGES = frozenset(
    {
        "ar",
        "cs",
        "de",
        "es",
        "fr",
        "ja",
        "ko",
        "nl",
        "pl",
        "pt_BR",
        "ru",
        "tr",
        "uk",
        "zh_CN",
    }
)
_IMPLICIT_PLURAL_FIELD_INDEXES = {
    # Arabic normally spells out zero, one, and two in the noun phrase, so
    # those forms can legitimately omit numeric fields and other values whose
    # meaning is fully implied by the selected form. Numeric plural forms must
    # retain every source field.
    "ar": frozenset({0, 1, 2}),
}
_INTERACTIVE_HOTKEY_GROUPS = (
    (
        ("yes hotkey", "y"),
        ("no hotkey", "n"),
    ),
    (
        ("yes hotkey", "y"),
        ("next hotkey", "n"),
        ("reset hotkey", "r"),
    ),
)
_ACTION_ALIAS_GROUPS = (
    (
        "confirmation",
        (
            ("yes", "y"),
            ("no", "n"),
        ),
        {
            "yes": "y",
            "no": "n",
        },
    ),
    (
        "action prompt",
        (
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
        ),
        {
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
        },
    ),
    (
        "file review",
        (
            ("include", "i"),
            ("skip", "s"),
            ("discard", "d"),
            ("replace", "r"),
            ("include file", "I"),
            ("skip file", "S"),
            ("discard file", "D"),
            ("block", "B"),
            ("unblock", "U"),
            ("fixup", "x"),
            ("candidates", "c"),
            ("candidate", "c"),
            ("next", "n"),
            ("previous", "p"),
            ("page", "g"),
            ("open", "o"),
            ("back", "q"),
            ("quit", "q"),
            ("help", "?"),
        ),
        {
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
        },
    ),
    (
        "batch menu",
        (
            ("create", "c"),
            ("edit", "e"),
            ("drop", "d"),
            ("apply", "a"),
            ("sift", "s"),
        ),
        {
            "create": "c",
            "edit": "e",
            "drop": "d",
            "apply": "a",
            "sift": "s",
        },
    ),
    (
        "fixup prompt",
        (
            ("yes", "y"),
            ("next", "n"),
            ("reset", "r"),
            ("cancel", "q"),
            ("quit", "q"),
        ),
        {
            "yes": "y",
            "next": "n",
            "reset": "r",
            "cancel": "q",
            "quit": "q",
        },
    ),
    (
        "candidate operation",
        (
            ("include", "i"),
            ("apply", "a"),
            ("quit", "q"),
        ),
        {
            "include": "i",
            "apply": "a",
            "quit": "q",
            "cancel": "q",
        },
    ),
    (
        "block target",
        (
            ("local exclude", "l"),
            ("quit", "q"),
        ),
        {
            "gitignore": "g",
            "local": "l",
            "local exclude": "l",
            "quit": "q",
            "cancel": "q",
        },
    ),
)
_DISPLAY_MARKERS = ("✓", "⚠", "❯")
_DIRECTIONAL_CONTROLS = frozenset(
    {
        "\u061c",  # ARABIC LETTER MARK
        "\u200e",  # LEFT-TO-RIGHT MARK
        "\u200f",  # RIGHT-TO-LEFT MARK
        "\u202a",  # LEFT-TO-RIGHT EMBEDDING
        "\u202b",  # RIGHT-TO-LEFT EMBEDDING
        "\u202c",  # POP DIRECTIONAL FORMATTING
        "\u202d",  # LEFT-TO-RIGHT OVERRIDE
        "\u202e",  # RIGHT-TO-LEFT OVERRIDE
        "\u2066",  # LEFT-TO-RIGHT ISOLATE
        "\u2067",  # RIGHT-TO-LEFT ISOLATE
        "\u2068",  # FIRST STRONG ISOLATE
        "\u2069",  # POP DIRECTIONAL ISOLATE
    }
)
_LEADING_SHORTCUT_PATTERN = re.compile(
    r"^  (?P<code>[A-Za-z?!])(?:<[^>]+>)?(?:,| {2,})"
)
_COMMAND_TOKEN_PATTERN = re.compile(
    r"git-stage-batch|(?<![\w-])--[a-z][a-z0-9-]*"
)
_TRANSLATION_CALL_NAMES = frozenset({"_", "ngettext", "pgettext", "npgettext"})
_FORMATTER = string.Formatter()


class _FormattedStringTranslationCallVisitor(ast.NodeVisitor):
    """Find translation calls that older xgettext releases cannot extract."""

    def __init__(self) -> None:
        self.formatted_string_depth = 0
        self.calls: list[tuple[int, str]] = []

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        """Track calls nested anywhere inside an f-string expression."""
        self.formatted_string_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self.formatted_string_depth -= 1

    def visit_Call(self, node: ast.Call) -> None:
        """Record direct gettext entry points reached inside an f-string."""
        if (
            self.formatted_string_depth
            and isinstance(node.func, ast.Name)
            and node.func.id in _TRANSLATION_CALL_NAMES
        ):
            self.calls.append((node.lineno, node.func.id))
        self.generic_visit(node)


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def _require_gettext_tools() -> None:
    missing = [
        tool
        for tool in ("msgattrib", "msgcmp", "msgfmt", "xgettext")
        if shutil.which(tool) is None
    ]
    if missing:
        raise RuntimeError("translation checks require: " + ", ".join(missing))


def _expected_potfiles() -> list[str]:
    return find_translatable_files(PROJECT_ROOT / "src" / "git_stage_batch")


def _stored_potfiles() -> list[str]:
    return [
        line
        for line in POTFILES_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


def _check_portable_translation_calls() -> list[str]:
    """Return translation calls that GNU gettext 0.21 cannot discover."""
    failures: list[str] = []
    for relative_path in _expected_potfiles():
        source_path = PROJECT_ROOT / relative_path
        try:
            tree = ast.parse(source_path.read_bytes(), filename=relative_path)
        except SyntaxError as error:
            line = error.lineno or 1
            failures.append(f"{relative_path}:{line}: could not parse source")
            continue

        visitor = _FormattedStringTranslationCallVisitor()
        visitor.visit(tree)
        failures.extend(
            f"{relative_path}:{line}: {name}() is nested inside an f-string"
            for line, name in visitor.calls
        )
    return failures


def _language_codes() -> list[str]:
    return [
        line
        for line in (PO_DIRECTORY / "LINGUAS").read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]


def _locale_uses_rtl_writing_direction(language: str) -> bool:
    """Return whether a catalog locale conventionally uses an RTL script."""
    locale_with_modifier = language.split(".", 1)[0]
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


def _write_current_template(output_path: Path) -> None:
    result = _run(
        [
            "xgettext",
            "--language=Python",
            "--from-code=UTF-8",
            "--keyword=_",
            "--keyword=ngettext:1,2",
            "--keyword=pgettext:1c,2",
            "--keyword=npgettext:1c,2,3",
            "--files-from=po/POTFILES.in",
            f"--output={output_path}",
        ]
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "xgettext failed")


def _text_outside_directional_isolates(text: str) -> str:
    """Return text outside balanced Unicode directional isolates."""
    depth = 0
    outside: list[str] = []
    for character in text:
        if character in _DIRECTIONAL_ISOLATE_STARTS:
            depth += 1
        elif character == _POP_DIRECTIONAL_ISOLATE:
            if depth == 0:
                raise ValueError("unmatched POP DIRECTIONAL ISOLATE")
            depth -= 1
        elif depth == 0:
            outside.append(character)
    if depth:
        raise ValueError("unclosed directional isolate")
    return "".join(outside)


def _format_literals(template: str) -> str:
    """Return literal text while omitting Python replacement fields."""
    literals: list[str] = []
    try:
        for literal, _field_name, _format_spec, _conversion in _FORMATTER.parse(
            template
        ):
            literals.append(literal)
    except ValueError:
        # A literal brace in a message that is never formatted is valid text.
        return template
    return "".join(literals)


def _check_rtl_catalog(compiled_path: Path) -> list[str]:
    """Return RTL safety failures from one compiled gettext catalog."""
    with compiled_path.open("rb") as stream:
        catalog = gettext.GNUTranslations(stream)._catalog

    failures: list[str] = []
    seen: set[str] = set()
    for translated in catalog.values():
        if not isinstance(translated, str) or translated in seen:
            continue
        seen.add(translated)
        if not any(
            unicodedata.bidirectional(character) in {"R", "AL"}
            for character in translated
        ):
            continue
        try:
            outside = _text_outside_directional_isolates(translated)
        except ValueError as error:
            failures.append(f"{error}: {translated!r}")
            continue
        outside = _format_literals(outside)
        unsafe_index = next(
            (
                index
                for index, character in enumerate(outside)
                if unicodedata.bidirectional(character) in {"L", "EN"}
            ),
            None,
        )
        if unsafe_index is not None:
            token = outside[unsafe_index:].split(maxsplit=1)[0]
            failures.append(
                f"unisolated left-to-right token {token!r}: {translated!r}"
            )
    return failures


def _format_field_first_component(field_name: str) -> str:
    """Return the argument-name portion before attribute or item traversal."""
    end = len(field_name)
    for separator in (".", "["):
        separator_index = field_name.find(separator)
        if separator_index >= 0:
            end = min(end, separator_index)
    return field_name[:end]


def _normalized_format_field(
    field_name: str,
    format_spec: str,
    conversion: str | None,
    automatic_index: list[int | None],
) -> tuple[object, ...]:
    """Return one hashable field signature with automatic indexes resolved."""
    first_component = _format_field_first_component(field_name)
    if first_component == "":
        if automatic_index[0] is None:
            raise ValueError(
                "cannot switch from manual field specification to automatic "
                "field numbering"
            )
        field_name = str(automatic_index[0]) + field_name
        automatic_index[0] += 1
    elif first_component.isdecimal():
        if automatic_index[0] not in (0, None):
            raise ValueError(
                "cannot switch from automatic field numbering to manual "
                "field specification"
            )
        automatic_index[0] = None
        field_name = str(int(first_component)) + field_name[len(first_component):]

    normalized_spec = _normalized_format_spec(format_spec, automatic_index)
    return field_name, conversion or "", normalized_spec


def _normalized_format_spec(
    format_spec: str,
    automatic_index: list[int | None],
) -> tuple[tuple[object, ...], ...]:
    """Return a structural signature for a possibly nested format specifier."""
    parts: list[tuple[object, ...]] = []
    for literal, field_name, nested_spec, conversion in _FORMATTER.parse(format_spec):
        if literal:
            parts.append(("literal", literal))
        if field_name is not None:
            parts.append((
                "field",
                _normalized_format_field(
                    field_name,
                    nested_spec,
                    conversion,
                    automatic_index,
                ),
            ))
    return tuple(parts)


def _format_field_signatures(template: str) -> Counter[tuple[object, ...]]:
    """Return the multiset of Python format fields used by one template."""
    automatic_index: list[int | None] = [0]
    signatures: Counter[tuple[object, ...]] = Counter()
    for _literal, field_name, format_spec, conversion in _FORMATTER.parse(template):
        if field_name is not None:
            signatures[
                _normalized_format_field(
                    field_name,
                    format_spec,
                    conversion,
                    automatic_index,
                )
            ] += 1
    return signatures


def _message_id_without_context(message_id: str) -> str:
    """Remove the GNU gettext context prefix from one compiled key."""
    return message_id.split("\x04", 1)[-1]


def _check_compiled_format_fields(
    compiled_path: Path,
    *,
    language: str,
) -> list[str]:
    """Return placeholder mismatches missed by gettext's format flagging."""
    with compiled_path.open("rb") as stream:
        catalog = gettext.GNUTranslations(stream)._catalog

    failures: list[str] = []
    seen: set[tuple[str, str]] = set()
    for raw_message_id, translated in catalog.items():
        if not isinstance(translated, str):
            continue
        is_plural = isinstance(raw_message_id, tuple)
        message_id = (
            raw_message_id[0]
            if is_plural
            else raw_message_id
        )
        if not isinstance(message_id, str) or not message_id:
            continue
        message_id = _message_id_without_context(message_id)
        pair = (message_id, translated)
        if pair in seen:
            continue
        seen.add(pair)
        try:
            source_fields = _format_field_signatures(message_id)
        except ValueError:
            # A literal brace in a message that is never formatted is valid text.
            continue
        try:
            translated_fields = _format_field_signatures(translated)
        except ValueError as error:
            failures.append(
                f"invalid translated Python format for {message_id!r}: {error}"
            )
            continue
        plural_index = (
            raw_message_id[1]
            if is_plural and isinstance(raw_message_id[1], int)
            else None
        )
        may_omit_fields = plural_index in _IMPLICIT_PLURAL_FIELD_INDEXES.get(
            language,
            (),
        )
        fields_are_valid = (
            translated_fields <= source_fields
            if may_omit_fields
            else translated_fields == source_fields
        )
        if not fields_are_valid:
            failures.append(
                "Python format fields differ for "
                f"{message_id!r}: expected {source_fields}, got "
                f"{translated_fields}"
            )
    return failures


def _check_interactive_hotkeys(compiled_path: Path) -> list[str]:
    """Return unusable or ambiguous localized prompt-hotkey failures."""
    with compiled_path.open("rb") as stream:
        translations = gettext.GNUTranslations(stream)

    failures: list[str] = []
    for group in _INTERACTIVE_HOTKEY_GROUPS:
        translated_hotkeys = [
            (
                context,
                stable_code,
                translations.pgettext(context, stable_code),
            )
            for context, stable_code in group
        ]
        for context, _stable_code, translated in translated_hotkeys:
            if len(translated) != 1 or translated.isspace():
                failures.append(
                    f"{context!r} must translate to one non-whitespace "
                    f"character, got {translated!r}"
                )

        normalized = [translated.casefold() for _, _, translated in translated_hotkeys]
        if len(set(normalized)) != len(normalized):
            details = ", ".join(
                f"{context}={translated!r}"
                for context, _stable_code, translated in translated_hotkeys
            )
            failures.append(f"prompt hotkeys are ambiguous: {details}")

        stable_actions = {
            stable_code.casefold(): context for context, stable_code in group
        }
        for context, stable_code, translated in translated_hotkeys:
            claimed_context = stable_actions.get(translated.casefold())
            if claimed_context is not None and claimed_context != context:
                failures.append(
                    f"{context!r} translation {translated!r} conflicts with "
                    f"the stable key for {claimed_context!r}; expected its own "
                    f"stable key {stable_code!r} or a distinct localized key"
                )
    return failures


def _action_word_key(word: str) -> str:
    """Return the runtime-equivalent exact-match key for an action word."""
    return unicodedata.normalize("NFC", word).casefold()


def _action_word_without_marks(word: str) -> str:
    """Return the runtime-equivalent optional-diacritic action alias."""
    return "".join(
        character
        for character in unicodedata.normalize("NFKD", word)
        if not unicodedata.combining(character)
    )


def _check_action_aliases(compiled_path: Path) -> list[str]:
    """Return localized action aliases that runtime parsing cannot honor."""
    with compiled_path.open("rb") as stream:
        translations = gettext.GNUTranslations(stream)

    failures: list[str] = []
    for group_name, words_and_actions, legacy_words in _ACTION_ALIAS_GROUPS:
        aliases = [
            (message_id, action, translations.gettext(message_id))
            for message_id, action in words_and_actions
        ]
        exact_candidates: dict[str, list[tuple[str, str, str]]] = {}
        unmarked_candidates: dict[str, list[tuple[str, str, str]]] = {}
        stable_codes = {action for _message_id, action in words_and_actions}
        for message_id, action, translated in aliases:
            exact = _action_word_key(translated)
            exact_candidates.setdefault(exact, []).append(
                (message_id, action, translated)
            )
            unmarked = _action_word_without_marks(exact)
            if unmarked != exact:
                unmarked_candidates.setdefault(unmarked, []).append(
                    (message_id, action, translated)
                )

            stable_action = translated if translated in stable_codes else None
            if stable_action is not None and stable_action != action:
                failures.append(
                    f"{group_name} alias {translated!r} for {message_id!r} "
                    f"is shadowed by stable action {stable_action!r}"
                )

            legacy_action = legacy_words.get(exact)
            if legacy_action is not None and legacy_action != action:
                failures.append(
                    f"{group_name} alias {translated!r} for {message_id!r} "
                    f"is shadowed by legacy action {legacy_action!r}"
                )

        for alias, entries in exact_candidates.items():
            actions = {action for _message_id, action, _translated in entries}
            if len(actions) < 2:
                continue
            details = ", ".join(
                f"{message_id}={translated!r} ({action})"
                for message_id, action, translated in entries
            )
            failures.append(
                f"{group_name} exact alias {alias!r} is ambiguous: {details}"
            )

        for alias, entries in unmarked_candidates.items():
            combined_entries = entries + exact_candidates.get(alias, [])
            actions = {
                action
                for _message_id, action, _translated in combined_entries
            }
            if len(actions) < 2:
                continue
            details = ", ".join(
                f"{message_id}={translated!r} ({action})"
                for message_id, action, translated in combined_entries
            )
            failures.append(
                f"{group_name} optional-diacritic alias {alias!r} is "
                f"ambiguous: {details}"
            )
    return failures


def _without_directional_controls(text: str) -> str:
    """Remove bidi formatting controls before checking fixed-position text."""
    return "".join(
        character for character in text if character not in _DIRECTIONAL_CONTROLS
    )


def _check_compiled_display_contracts(compiled_path: Path) -> list[str]:
    """Return visible marker and stable shortcut changes in translations."""
    with compiled_path.open("rb") as stream:
        catalog = gettext.GNUTranslations(stream)._catalog

    failures: list[str] = []
    seen: set[tuple[str, str]] = set()
    for raw_message_id, translated in catalog.items():
        if not isinstance(translated, str):
            continue
        message_id = (
            raw_message_id[0]
            if isinstance(raw_message_id, tuple)
            else raw_message_id
        )
        if not isinstance(message_id, str) or not message_id:
            continue
        message_id = _message_id_without_context(message_id)
        item = (message_id, translated)
        if item in seen:
            continue
        seen.add(item)

        source_markers = Counter(
            {
                marker: count
                for marker in _DISPLAY_MARKERS
                if (count := message_id.count(marker))
            }
        )
        translated_markers = Counter(
            {
                marker: count
                for marker in _DISPLAY_MARKERS
                if (count := translated.count(marker))
            }
        )
        if translated_markers != source_markers:
            failures.append(
                f"display markers differ for {message_id!r}: expected "
                f"{source_markers}, got {translated_markers}"
            )

        source_without_controls = _without_directional_controls(message_id)
        shortcut = _LEADING_SHORTCUT_PATTERN.match(source_without_controls)
        if shortcut is None:
            continue
        translated_without_controls = _without_directional_controls(translated)
        translated_shortcut = _LEADING_SHORTCUT_PATTERN.match(
            translated_without_controls
        )
        expected_code = shortcut.group("code")
        actual_code = (
            translated_shortcut.group("code")
            if translated_shortcut is not None
            else None
        )
        if actual_code != expected_code:
            failures.append(
                f"leading shortcut differs for {message_id!r}: expected "
                f"{expected_code!r}, got {actual_code!r} in {translated!r}"
            )
    return failures


def _check_compiled_command_tokens(
    compiled_path: Path,
    *,
    language: str,
) -> list[str]:
    """Return command-name and long-option omissions or substitutions."""
    with compiled_path.open("rb") as stream:
        catalog = gettext.GNUTranslations(stream)._catalog

    failures: list[str] = []
    seen: set[tuple[str, int | None, str]] = set()
    for raw_message_id, translated in catalog.items():
        if not isinstance(translated, str):
            continue
        is_plural = isinstance(raw_message_id, tuple)
        message_id = raw_message_id[0] if is_plural else raw_message_id
        if not isinstance(message_id, str) or not message_id:
            continue
        message_id = _message_id_without_context(message_id)
        plural_index = (
            raw_message_id[1]
            if is_plural and isinstance(raw_message_id[1], int)
            else None
        )
        item = (message_id, plural_index, translated)
        if item in seen:
            continue
        seen.add(item)

        source_tokens = Counter(_COMMAND_TOKEN_PATTERN.findall(message_id))
        translated_tokens = Counter(_COMMAND_TOKEN_PATTERN.findall(translated))
        may_omit_tokens = plural_index in _IMPLICIT_PLURAL_FIELD_INDEXES.get(
            language,
            (),
        )
        tokens_are_valid = (
            translated_tokens <= source_tokens
            if may_omit_tokens
            else translated_tokens == source_tokens
        )
        if not tokens_are_valid:
            failures.append(
                f"command tokens differ for {message_id!r}: expected "
                f"{source_tokens}, got {translated_tokens}"
            )
    return failures


def check_translations() -> list[str]:
    """Return human-readable translation consistency failures."""
    _require_gettext_tools()
    failures: list[str] = []
    if _stored_potfiles() != _expected_potfiles():
        failures.append("po/POTFILES.in is stale; run scripts/generate_potfiles.py")
    portable_call_failures = _check_portable_translation_calls()
    if portable_call_failures:
        failures.append(
            "source has translation calls that GNU gettext 0.21 cannot extract:\n"
            + "\n".join(portable_call_failures)
        )

    with tempfile.TemporaryDirectory(prefix="git-stage-batch-i18n-") as temp:
        template_path = Path(temp) / "git-stage-batch.pot"
        _write_current_template(template_path)
        for language in _language_codes():
            catalog_path = PO_DIRECTORY / f"{language}.po"
            compiled_path = Path(temp) / f"{language}.mo"
            compile_result = _run(
                [
                    "msgfmt",
                    "--check",
                    "--check-format",
                    f"--output-file={compiled_path}",
                    str(catalog_path),
                ]
            )
            if compile_result.returncode:
                failures.append(
                    f"{catalog_path.relative_to(PROJECT_ROOT)} does not compile:\n"
                    f"{compile_result.stderr.strip()}"
                )
                continue

            if language in _REQUIRED_COMPLETE_LANGUAGES:
                for selection, description in (
                    ("--untranslated", "untranslated messages"),
                    ("--only-fuzzy", "fuzzy messages"),
                ):
                    selected_path = Path(temp) / (
                        f"{language}-{selection.removeprefix('--')}.po"
                    )
                    selected_result = _run(
                        [
                            "msgattrib",
                            selection,
                            "--no-obsolete",
                            f"--output-file={selected_path}",
                            str(catalog_path),
                        ]
                    )
                    if selected_result.returncode:
                        failures.append(
                            f"{catalog_path.relative_to(PROJECT_ROOT)} could not "
                            f"be checked for {description}:\n"
                            f"{selected_result.stderr.strip()}"
                        )
                    elif selected_path.exists():
                        failures.append(
                            f"{catalog_path.relative_to(PROJECT_ROOT)} has "
                            f"{description}"
                        )

            format_failures = _check_compiled_format_fields(
                compiled_path,
                language=language,
            )
            if format_failures:
                failures.append(
                    f"{catalog_path.relative_to(PROJECT_ROOT)} has invalid "
                    "Python format fields:\n" + "\n".join(format_failures)
                )

            hotkey_failures = _check_interactive_hotkeys(compiled_path)
            if hotkey_failures:
                failures.append(
                    f"{catalog_path.relative_to(PROJECT_ROOT)} has invalid "
                    "interactive hotkeys:\n" + "\n".join(hotkey_failures)
                )

            action_alias_failures = _check_action_aliases(compiled_path)
            if action_alias_failures:
                failures.append(
                    f"{catalog_path.relative_to(PROJECT_ROOT)} has invalid "
                    "localized action aliases:\n"
                    + "\n".join(action_alias_failures)
                )

            display_contract_failures = _check_compiled_display_contracts(
                compiled_path
            )
            if display_contract_failures:
                failures.append(
                    f"{catalog_path.relative_to(PROJECT_ROOT)} has invalid "
                    "display markers or shortcuts:\n"
                    + "\n".join(display_contract_failures)
                )

            command_token_failures = _check_compiled_command_tokens(
                compiled_path,
                language=language,
            )
            if command_token_failures:
                failures.append(
                    f"{catalog_path.relative_to(PROJECT_ROOT)} has invalid "
                    "command tokens:\n" + "\n".join(command_token_failures)
                )

            if _locale_uses_rtl_writing_direction(language):
                rtl_failures = _check_rtl_catalog(compiled_path)
                if rtl_failures:
                    failures.append(
                        f"{catalog_path.relative_to(PROJECT_ROOT)} has unsafe "
                        "bidirectional text:\n" + "\n".join(rtl_failures)
                    )

            synchronization_result = _run(
                [
                    "msgcmp",
                    "--no-fuzzy-matching",
                    str(catalog_path),
                    str(template_path),
                ]
            )
            synchronization_details = synchronization_result.stderr.strip()
            if synchronization_result.returncode or synchronization_details:
                failures.append(
                    f"{catalog_path.relative_to(PROJECT_ROOT)} is not "
                    "synchronized with source:\n"
                    f"{synchronization_details[:4000]}"
                )
    return failures


def main() -> int:
    """Run strict catalog checks."""
    try:
        failures = check_translations()
    except RuntimeError as error:
        print(f"translation check failed: {error}", file=sys.stderr)
        return 1
    if failures:
        print("\n\n".join(failures), file=sys.stderr)
        return 1
    print("Translation catalogs are synchronized and valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
