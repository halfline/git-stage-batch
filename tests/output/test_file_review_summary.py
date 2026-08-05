"""Tests for localized file-review summary counts."""

from git_stage_batch.data.file_review.records import ReviewSource
from git_stage_batch.output import file_review_summary


def test_change_summary_uses_the_exact_shown_change_count(monkeypatch) -> None:
    selected_counts = []

    def record_ngettext(singular, plural, count):
        selected_counts.append(count)
        return singular if count == 1 else plural

    monkeypatch.setattr(file_review_summary, "ngettext", record_ngettext)

    summary = file_review_summary.change_summary("1–4", 4, 7)

    assert summary == "changes 1–4/7"
    assert selected_counts == [4]


def test_batch_source_summary_isolates_the_dynamic_batch_name(monkeypatch) -> None:
    monkeypatch.setattr(
        file_review_summary,
        "bidi_isolate",
        lambda value: f"<isolate>{value}</isolate>",
    )

    summary = file_review_summary.review_source_summary(
        ReviewSource.BATCH,
        "feature/one",
        "Changes: batch feature/one",
    )

    assert summary == "<isolate>feature/one</isolate>"
