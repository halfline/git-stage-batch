"""Batch name validation and existence checking."""

from __future__ import annotations

from functools import lru_cache

from .ref_names import (
    BATCH_CONTENT_REF_PREFIX,
    BATCH_STATE_REF_PREFIX,
    LEGACY_BATCH_REF_PREFIX,
)
from ..exceptions import CommandError
from ..i18n import _
from ..utils.git_command import run_git_command
from ..utils.paths import get_batches_directory_path


MAX_BATCH_NAME_BYTES = 250


@lru_cache(maxsize=512)
def _git_accepts_batch_name(name: str) -> bool:
    """Return whether Git accepts every ref namespace used for a batch."""
    prefixes = (
        BATCH_CONTENT_REF_PREFIX,
        BATCH_STATE_REF_PREFIX,
        LEGACY_BATCH_REF_PREFIX,
    )
    return all(
        run_git_command(
            ["check-ref-format", f"{prefix}{name}"],
            check=False,
            requires_index_lock=False,
        ).returncode == 0
        for prefix in prefixes
    )


def validate_batch_name(name: str) -> None:
    """Validate a batch name against product and Git ref requirements."""
    if not name:
        raise CommandError(_("Batch name cannot be empty"))

    # Keep the established product restrictions and their specific diagnostics.
    invalid_chars = ['/', '\\', '..', ':', ' ', '\t', '\n', '\r']
    for char in invalid_chars:
        if char in name:
            raise CommandError(_("Batch name cannot contain: {char}").format(char=repr(char)))

    if name.startswith('.'):
        raise CommandError(_("Batch name cannot start with '.'"))

    if len(name.encode("utf-8")) > MAX_BATCH_NAME_BYTES:
        raise CommandError(
            _("Batch name cannot exceed {limit} UTF-8 bytes").format(
                limit=MAX_BATCH_NAME_BYTES
            )
        )

    if not _git_accepts_batch_name(name):
        raise CommandError(
            _("Batch name '{name}' is not compatible with Git ref naming rules").format(
                name=name
            )
        )


def invalid_file_backed_batch_names() -> list[str]:
    """Return legacy metadata names that cannot be used by current storage."""
    batches_directory = get_batches_directory_path()
    if not batches_directory.is_dir():
        return []

    invalid_names = []
    for metadata_path in batches_directory.rglob("metadata.json"):
        batch_name = metadata_path.parent.relative_to(batches_directory).as_posix()
        try:
            validate_batch_name(batch_name)
        except CommandError:
            invalid_names.append(batch_name)
    return sorted(invalid_names)


def batch_exists(batch_name: str) -> bool:
    """Check if a batch exists by checking its authoritative git ref."""
    validate_batch_name(batch_name)
    result = run_git_command(
        ["show-ref", "--verify", "--quiet", f"{BATCH_CONTENT_REF_PREFIX}{batch_name}"],
        check=False,
        requires_index_lock=False,
    )
    if result.returncode == 0:
        return True

    result = run_git_command(
        ["show-ref", "--verify", "--quiet", f"{LEGACY_BATCH_REF_PREFIX}{batch_name}"],
        check=False,
        requires_index_lock=False,
    )
    return result.returncode == 0
