"""Git start-point discovery for commit-backed and unborn repositories."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from ..exceptions import CommandError
from ..i18n import _
from .file_io import read_text_file_contents, write_text_file_contents
from .git_command import run_git_command
from .git_object_io import get_empty_git_tree_object_id
from .paths import get_session_start_point_file_path


@dataclass(frozen=True)
class SessionStartPoint:
    """Repository identity and index state captured before session mutation."""

    head_commit: str | None
    symbolic_head: str | None
    index_tree: str

    @property
    def is_unborn(self) -> bool:
        return self.head_commit is None


def resolve_session_start_point() -> SessionStartPoint:
    """Capture HEAD identity and the exact current index tree."""
    head_result = run_git_command(
        ["rev-parse", "--verify", "HEAD^{commit}"],
        check=False,
        requires_index_lock=False,
    )
    head_commit = head_result.stdout.strip() if head_result.returncode == 0 else None
    symbolic_result = run_git_command(
        ["symbolic-ref", "-q", "HEAD"],
        check=False,
        requires_index_lock=False,
    )
    symbolic_head = (
        symbolic_result.stdout.strip() if symbolic_result.returncode == 0 else None
    )
    if head_commit is None and symbolic_head is None:
        raise CommandError(_("Cannot determine the unborn branch for HEAD."))
    index_result = run_git_command(
        ["write-tree"],
        check=False,
        requires_index_lock=False,
    )
    if index_result.returncode != 0:
        raise CommandError(
            _("Cannot snapshot the index before starting the session: {error}").format(
                error=index_result.stderr.strip() or _("git write-tree failed")
            )
        )
    return SessionStartPoint(
        head_commit=head_commit,
        symbolic_head=symbolic_head,
        index_tree=index_result.stdout.strip(),
    )


def save_session_start_point(start_point: SessionStartPoint) -> None:
    """Persist a start point before publishing the active-session marker."""
    write_text_file_contents(
        get_session_start_point_file_path(),
        json.dumps(asdict(start_point), indent=2, sort_keys=True) + "\n",
    )


def load_session_start_point() -> SessionStartPoint:
    """Load the current session start point, including legacy sessions."""
    path = get_session_start_point_file_path()
    if path.exists():
        try:
            data = json.loads(read_text_file_contents(path))
            return SessionStartPoint(
                head_commit=data.get("head_commit"),
                symbolic_head=data.get("symbolic_head"),
                index_tree=data["index_tree"],
            )
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise CommandError(_("Session start-point metadata is invalid.")) from error

    from .paths import get_abort_head_file_path

    legacy_head = read_text_file_contents(get_abort_head_file_path()).strip()
    if not legacy_head:
        raise CommandError(_("Session start-point metadata is missing."))
    tree = run_git_command(
        ["rev-parse", f"{legacy_head}^{{tree}}"],
        requires_index_lock=False,
    ).stdout.strip()
    return SessionStartPoint(legacy_head, None, tree)


def session_comparison_base() -> str:
    """Return HEAD when it exists, otherwise the empty tree."""
    result = run_git_command(
        ["rev-parse", "--verify", "HEAD^{commit}"],
        check=False,
        requires_index_lock=False,
    )
    return (
        result.stdout.strip()
        if result.returncode == 0
        else get_empty_git_tree_object_id()
    )


def current_head_commit() -> str | None:
    """Return the selected HEAD commit, or None while HEAD is unborn."""
    result = run_git_command(
        ["rev-parse", "--verify", "HEAD^{commit}"],
        check=False,
        requires_index_lock=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def require_repository_history() -> None:
    """Reject a history-dependent operation while HEAD is unborn."""
    if current_head_commit() is None:
        raise CommandError(
            _("This command requires at least one commit; HEAD is still unborn.")
        )
