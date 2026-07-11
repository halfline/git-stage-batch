"""Tests for byte-safe Git pathname helpers."""

import pytest

from git_stage_batch.exceptions import CommandError
from git_stage_batch.git_paths import (
    decode_path,
    encode_path,
    nul_records,
    quote_path_token,
    unquote_path_token,
)


def test_path_conversion_round_trips_non_utf8_bytes():
    raw_path = b"name-\xff"

    assert encode_path(decode_path(raw_path)) == raw_path


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


@pytest.mark.parametrize("token", [b'"unfinished', b'"bad\\x"', b'"bad\\"'])
def test_invalid_git_c_quoted_paths_are_rejected(token):
    with pytest.raises(CommandError):
        unquote_path_token(token)
