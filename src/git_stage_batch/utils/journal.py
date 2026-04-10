"""Debug journal for tracking all operations."""

from __future__ import annotations

import json
import os
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any

from .git import run_git_command
from .paths import get_state_directory_path


def _get_journal_path() -> Path:
    """Get path to journal file."""
    return get_state_directory_path() / "journal.jsonl"


def _get_index_state(file_path: str | None = None) -> dict[str, Any]:
    """Get current index state for a file or all files."""
    try:
        if file_path:
            ls_result = run_git_command(["ls-files", "--stage", "--", file_path], check=False)
        else:
            ls_result = run_git_command(["ls-files", "--stage"], check=False)

        if ls_result.stdout.strip():
            entries = []
            for line in ls_result.stdout.strip().split('\n'):
                parts = line.split()
                if len(parts) >= 4:
                    entries.append({
                        "mode": parts[0],
                        "hash": parts[1],
                        "stage": parts[2],
                        "path": parts[3]
                    })
            return entries[0] if file_path and entries else entries
        return {"status": "not_in_index"} if file_path else []
    except Exception as e:
        return {"error": str(e)}


def log_journal(operation: str, **kwargs: Any) -> None:
    """Log an operation to the journal.

    Args:
        operation: Name of the operation
        **kwargs: Additional context to log
    """
    try:
        journal_path = _get_journal_path()
        journal_path.parent.mkdir(parents=True, exist_ok=True)

        # Add stack trace to show call chain
        stack = traceback.extract_stack()
        # Filter to only our code
        filtered_stack = [
            {"file": s.filename.split("/")[-1], "line": s.lineno, "func": s.name}
            for s in stack
            if "git_stage_batch" in s.filename
        ][-5:]  # Last 5 frames

        entry = {
            "timestamp": datetime.now().isoformat(),
            "pid": os.getpid(),
            "operation": operation,
            "stack": filtered_stack,
            **kwargs
        }

        with open(journal_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        # Never let journal logging break the actual operation
        pass
