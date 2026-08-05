"""Tests for localized replacement batch-source errors."""

import pytest

from git_stage_batch.batch import replacement as replacement_module
from git_stage_batch.batch.ownership.absence_claims import AbsenceClaim
from git_stage_batch.batch.ownership.model import BatchOwnership
from git_stage_batch.batch.replacement import build_replacement_batch_view_from_lines


@pytest.mark.parametrize(
    ("ownership", "message"),
    [
        (
            BatchOwnership.from_presence_lines(["1", "3"], []),
            (
                "Replacement selection must resolve to one contiguous "
                "batch-source line range."
            ),
        ),
        (
            BatchOwnership(
                [],
                [
                    AbsenceClaim(anchor_line=1, content_lines=[b"old-one\n"]),
                    AbsenceClaim(anchor_line=2, content_lines=[b"old-two\n"]),
                ],
            ),
            (
                "Replacement selection must resolve to one contiguous "
                "batch-source region."
            ),
        ),
    ],
)
def test_replacement_selection_errors_are_localized(
    monkeypatch,
    ownership,
    message,
):
    translated_messages = []

    def translate(candidate):
        translated_messages.append(candidate)
        return f"localized: {candidate}"

    monkeypatch.setattr(replacement_module, "_", translate)

    with pytest.raises(ValueError) as error:
        build_replacement_batch_view_from_lines(
            [b"one\n", b"two\n", b"three\n"],
            ownership,
            "new",
        )

    assert str(error.value) == f"localized: {message}"
    assert translated_messages == [message]
