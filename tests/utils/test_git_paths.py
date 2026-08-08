"""Tests for byte-safe Git pathname helpers."""

import json

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.git_paths import (
    decode_path,
    display_path,
    encode_path,
    nul_records,
    quote_path_token,
    terminal_safe_text,
    terminal_safe_shell_join,
    terminal_safe_shell_quote,
    unquote_path_token,
)


def test_path_conversion_round_trips_non_utf8_bytes():
    raw_path = b"name-\xff"

    assert encode_path(decode_path(raw_path)) == raw_path


def test_display_path_keeps_printable_unicode_readable():
    assert display_path("café.txt") == "café.txt"


def test_display_path_quotes_control_characters_without_escaping_unicode():
    assert display_path("café\n.txt") == '"café\\n.txt"'


def test_display_path_escapes_bidirectional_controls() -> None:
    path = "before\u202eafter\u2069.txt"

    displayed = display_path(path)

    assert displayed == '"before\\u202eafter\\u2069.txt"'
    assert json.loads(displayed) == path


def test_terminal_safe_text_escapes_terminal_and_bidirectional_controls():
    value = "subject\x1b[2J\u202ereversed\u2069"

    displayed = terminal_safe_text(value)

    assert "\x1b" not in displayed
    assert "\u202e" not in displayed
    assert json.loads(displayed) == value


def test_terminal_safe_shell_quote_preserves_printable_arguments():
    """Printable values should retain conventional POSIX shell quoting."""
    assert terminal_safe_shell_quote("file name.txt") == "'file name.txt'"


def test_terminal_safe_shell_quote_escapes_control_and_non_utf8_bytes():
    """Displayed shell arguments must contain no literal terminal controls."""
    value = decode_path(b"evil\x1b[2Jname\nbyte-\xff.txt")

    quoted = terminal_safe_shell_quote(value)

    assert quoted == r"$'evil\e[2Jname\nbyte-\377.txt'"
    assert "\x1b" not in quoted
    assert "\n" not in quoted


def test_terminal_safe_shell_join_quotes_each_argument_safely():
    value = decode_path(b"line\nbyte-\xff")

    command = terminal_safe_shell_join(["include", "--file", value])

    assert command == r"include --file $'line\nbyte-\377'"
    assert "\n" not in command


def test_nul_records_preserve_newlines_and_empty_path_components():
    assert nul_records(b"first\nname\0trailing space \0") == [
        b"first\nname",
        b"trailing space ",
    ]


def test_git_c_quoted_path_decodes_escapes_and_octal_bytes():
    assert unquote_path_token(b'"tab\\tquote\\"slash\\\\byte\\377"') == (
        b'tab\tquote"slash\\byte\xff'
    )


def test_git_c_quoted_path_round_trips_raw_bytes():
    raw_path = b'space tab\tline\nquote"slash\\byte\xff'

    assert unquote_path_token(quote_path_token(raw_path)) == raw_path


@pytest.mark.parametrize(
    "token",
    [b'"unfinished', b'"bad\\x"', b'"bad\\"', b'"bad\\400path"'],
)
def test_invalid_git_c_quoted_paths_are_rejected(token):
    with pytest.raises(CommandError):
        unquote_path_token(token)
