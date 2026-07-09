"""Git help integration for CLI parsers."""

from __future__ import annotations

import argparse
import os
import tempfile
from contextlib import nullcontext
from importlib import resources
from pathlib import Path

from ..utils.command import run_command
from ..utils.git import run_git_command


class GitHelpArgumentParser(argparse.ArgumentParser):
    """Custom ArgumentParser that tries to use git help for --help."""

    def __init__(
        self,
        *args,
        help_topic: str | None = None,
        **kwargs,
    ):
        self._git_help_topic = help_topic
        super().__init__(*args, **kwargs)

    def print_help(self, file=None):
        """Try to use git help, fall back to argparse help."""
        if (
            self._git_help_topic is not None
            and _show_git_stage_batch_help(self._git_help_topic)
        ):
            return

        # Fall back to standard argparse help
        super().print_help(file)


def _resolve_default_manpath() -> str | None:
    """Return the default manpath as if MANPATH were unset."""
    env = os.environ.copy()
    env.pop("MANPATH", None)
    try:
        result = run_command(
            ["manpath", "-q"],
            check=False,
            env=env,
        )
    except (FileNotFoundError, OSError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _build_manpath_with_packaged_page(man_root: Path, env: dict[str, str]) -> str:
    """Build a MANPATH preferring the packaged man page when available."""
    if env.get("MANPATH"):
        return f"{man_root}{os.pathsep}{env['MANPATH']}"

    default_manpath = _resolve_default_manpath()
    if default_manpath:
        return f"{man_root}{os.pathsep}{default_manpath}"

    return f"{man_root}{os.pathsep}{os.pathsep}"


def _try_git_help_with_environment(
    help_topic: str,
    env: dict[str, str] | None = None,
) -> bool:
    """Run git help for a git-stage-batch topic."""
    try:
        result = run_git_command(
            ["help", _git_help_name_for_help_topic(help_topic)],
            check=False,
            capture_stdout=False,
            env=env,
            requires_index_lock=False,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.returncode == 0


def _with_real_manpath_root(manpage_path: Path):
    """Yield a manpath root that contains the requested man page."""
    if manpage_path.parent.name == "man1":
        return nullcontext(manpage_path.parent.parent)

    class _TemporaryManRoot:
        def __enter__(self):
            self._temp_dir = tempfile.TemporaryDirectory(prefix="git-stage-batch-help-")
            temp_root = Path(self._temp_dir.name)
            temp_manpage = temp_root / "man1" / manpage_path.name
            temp_manpage.parent.mkdir(parents=True, exist_ok=True)
            temp_manpage.write_bytes(manpage_path.read_bytes())
            return temp_root

        def __exit__(self, exc_type, exc, tb):
            self._temp_dir.cleanup()
            return False

    return _TemporaryManRoot()


def _manpage_name_for_help_topic(help_topic: str) -> str:
    """Return the man page filename for a git help topic."""
    return f"git-{help_topic}.1"


def _git_help_name_for_help_topic(help_topic: str) -> str:
    """Return the git help argument for a git-stage-batch topic."""
    return _manpage_name_for_help_topic(help_topic).removesuffix(".1")


def _show_git_stage_batch_help(help_topic: str = "stage-batch") -> bool:
    """Show git-stage-batch help from packaged or system man pages."""
    try:
        packaged_manpage = resources.files("git_stage_batch").joinpath(
            "assets",
            "man",
            "man1",
            _manpage_name_for_help_topic(help_topic),
        )
    except (ModuleNotFoundError, FileNotFoundError):
        packaged_manpage = None

    if packaged_manpage is not None:
        try:
            with resources.as_file(packaged_manpage) as packaged_manpage_path:
                if packaged_manpage_path.exists():
                    with _with_real_manpath_root(packaged_manpage_path) as man_root:
                        env = os.environ.copy()
                        env["MANPATH"] = _build_manpath_with_packaged_page(
                            Path(man_root),
                            env,
                        )
                        if _try_git_help_with_environment(help_topic, env):
                            return True
        except FileNotFoundError:
            pass

    return _try_git_help_with_environment(help_topic)
