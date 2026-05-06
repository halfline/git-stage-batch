"""Non-interactive hunk-by-hunk and line-by-line staging for git."""

try:
    from ._version import __version__
except (ImportError, FileNotFoundError):
    try:
        from .utils.git import get_git_repository_root_path

        __version__ = (
            get_git_repository_root_path() / "VERSION"
        ).read_text(encoding="utf-8").strip()
    except Exception:
        __version__ = "unknown"

__all__ = ["__version__"]
