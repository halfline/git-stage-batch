"""Line row rendering for file review output."""

from __future__ import annotations

from ..core.models import LineEntry
from .colors import Colors
from .file_review_model import FileReviewModel


def maximum_display_id_digit_count(model: FileReviewModel) -> int:
    """Return the display ID width needed for one file-review model."""
    if model.display_id_by_selection_id is None:
        return model.line_changes.maximum_line_id_digit_count()
    if not model.display_id_by_selection_id:
        return 1
    return len(str(max(model.display_id_by_selection_id.values())))


def print_file_review_rows(
    rows: tuple[LineEntry, ...],
    maximum_digits: int,
    *,
    display_id_by_selection_id: dict[int, int] | None,
    allowed_selection_ids: set[int] | None = None,
) -> None:
    """Print rows for one file-review change fragment."""
    use_color = Colors.enabled()
    label_width = maximum_digits + 3
    for line in rows:
        is_gap_line = (
            line.id is None
            and line.kind == " "
            and line.old_line_number is None
            and line.new_line_number is None
            and line.source_line is None
        )
        if line.id is None or (
            allowed_selection_ids is not None
            and line.id not in allowed_selection_ids
        ):
            display_id = None
        elif display_id_by_selection_id is not None:
            display_id = display_id_by_selection_id.get(line.id)
        else:
            display_id = line.id
        if display_id is None:
            label = ""
        else:
            label = f"[#{display_id}]"
        padding = " " * max(0, label_width - len(label))
        row_text = f" {line.kind} {line.display_text()}"
        if not use_color:
            print(f"{label}{padding}{row_text}")
            continue

        if label:
            print(f"{Colors.GRAY}{label}{Colors.RESET}{padding}", end="")
        else:
            print(padding, end="")

        if line.kind == "+":
            print(f"{Colors.GREEN}{row_text}{Colors.RESET}")
        elif line.kind == "-":
            print(f"{Colors.RED}{row_text}{Colors.RESET}")
        elif is_gap_line:
            print(f"{Colors.GRAY}{row_text}{Colors.RESET}")
        else:
            print(row_text)
