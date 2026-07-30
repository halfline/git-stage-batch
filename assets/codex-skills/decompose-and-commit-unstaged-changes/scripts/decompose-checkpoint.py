#!/usr/bin/env python3
"""Record and inspect resumable decompose workflow state."""

from __future__ import annotations

import argparse
import datetime as _dt
import errno
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def default_state_dir() -> Path:
    override = os.environ.get("DECOMPOSE_STATE_DIR")
    if override:
        return Path(override).expanduser()
    try:
        repo_root = subprocess.check_output(
            ["git", "--no-optional-locks", "rev-parse", "--show-toplevel"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        repo_root = str(Path.cwd().resolve())
    return Path(repo_root) / ".git-stage-batch"


STATE_DIR = default_state_dir()
CHECKPOINT_PATH = STATE_DIR / "decompose-checkpoint.json"
PLAN_PATH = STATE_DIR / "decompose-plan.json"
CANDIDATE_PATH = STATE_DIR / "decompose-plan.candidate.json"
NARRATIVE_PATH = STATE_DIR / "decompose-narrative.md"
REFINEMENT_PATH = STATE_DIR / "decompose-refinement.md"
DECOMPOSE_STATE_PATHS = (
    CHECKPOINT_PATH,
    PLAN_PATH,
    CANDIDATE_PATH,
    NARRATIVE_PATH,
    REFINEMENT_PATH,
)
CHECKPOINT_MODES = {"full", "deconstruct", "reconstruct", "resume"}
CHECKPOINT_PHASES = {
    "started",
    "phase1-running",
    "phase1-candidate",
    "phase1-complete",
    "phase2-running",
    "phase2-complete",
    "phase3-running",
    "refine-history-running",
    "phase3-complete",
}


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def git_output(*args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "--no-optional-locks", *args],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except subprocess.CalledProcessError:
        return ""


def current_head() -> str:
    return git_output("rev-parse", "HEAD")


def resolve_commit(revision: str) -> str:
    """Resolve one revision to its full commit object name."""
    return git_output("rev-parse", "--verify", f"{revision}^{{commit}}")


def file_digest(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def require_safe_state_dir() -> None:
    """Refuse to read or mutate workflow state through a directory symlink."""
    if STATE_DIR.is_symlink():
        raise SystemExit(f"refusing to use symlinked state path: {STATE_DIR}")


def fsync_directory(path: Path) -> None:
    """Durably publish directory-entry changes where the platform permits."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        if os.name == "nt" or error.errno in (errno.EINVAL, errno.ENOTSUP):
            return
        raise
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            if error.errno not in (errno.EBADF, errno.EINVAL, errno.ENOTSUP):
                raise
    finally:
        os.close(descriptor)


def load_checkpoint() -> dict[str, Any]:
    require_safe_state_dir()
    if not os.path.lexists(CHECKPOINT_PATH):
        return {}
    if CHECKPOINT_PATH.is_symlink():
        return {"checkpoint_error": "checkpoint path is a symlink"}
    try:
        data = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        return {"checkpoint_error": str(error)}
    if not isinstance(data, dict):
        return {"checkpoint_error": "checkpoint must be a JSON object"}
    return data


def _nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def checkpoint_validation_error(data: dict[str, Any]) -> str | None:
    """Return why checkpoint state is unsafe to update, if applicable."""
    parse_error = data.get("checkpoint_error")
    if parse_error:
        return str(parse_error)
    if type(data.get("schema")) is not int or data["schema"] != 1:
        return "unsupported or missing schema"
    base = data.get("base")
    if not _nonempty_string(base):
        return "missing base"
    assert isinstance(base, str)
    resolved_base = resolve_commit(base)
    if not resolved_base:
        return "base does not name a commit"
    if resolved_base != base:
        return "base is not a full commit object name"
    if data.get("mode") not in CHECKPOINT_MODES:
        return "invalid mode"
    if data.get("phase") not in CHECKPOINT_PHASES:
        return "invalid phase"

    events = data.get("events")
    if not isinstance(events, list):
        return "events must be a list"
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            return f"event {index} must be an object"
        if not _nonempty_string(event.get("at")):
            return f"event {index} has no valid timestamp"
        if not _nonempty_string(event.get("event")):
            return f"event {index} has no valid kind"

    completed_batches = data.get("completed_batches")
    if not isinstance(completed_batches, list):
        return "completed batches must be a list"
    for index, batch_name in enumerate(completed_batches):
        if not _nonempty_string(batch_name):
            return f"completed batch {index} must be a nonempty string"

    commits = data.get("commits")
    if not isinstance(commits, list):
        return "commits must be a list"
    for index, commit in enumerate(commits):
        if not isinstance(commit, dict):
            return f"commit {index} must be an object"
        commit_sha = commit.get("sha")
        if not _nonempty_string(commit_sha):
            return f"commit {index} has no valid object name"
        assert isinstance(commit_sha, str)
        if resolve_commit(commit_sha) != commit_sha:
            return f"commit {index} does not name a full commit object"
        if not isinstance(commit.get("subject"), str):
            return f"commit {index} has no valid subject"

    if "current_batch" in data and not _nonempty_string(
        data["current_batch"]
    ):
        return "current batch must be a nonempty string"
    return None


def require_checkpoint() -> dict[str, Any]:
    """Load checkpoint state that is safe to update or resume."""
    if not os.path.lexists(CHECKPOINT_PATH):
        raise SystemExit("no decompose checkpoint; run start first")
    data = load_checkpoint()
    validation_error = checkpoint_validation_error(data)
    if validation_error is not None:
        raise SystemExit(
            f"invalid decompose checkpoint: {validation_error}"
        )
    return data


def list_batch_refs() -> list[str]:
    raw = git_output("for-each-ref", "--format=%(refname)", "refs/git-stage-batch/state")
    names: list[str] = []
    for ref in raw.splitlines():
        name = ref.rsplit("/", 1)[-1]
        if name.startswith("decompose-"):
            names.append(name)
    return sorted(set(names))


def commit_count_since(base: str | None) -> int | None:
    if not base:
        return None
    raw = git_output("rev-list", "--count", f"{base}..HEAD")
    try:
        return int(raw)
    except ValueError:
        return None


def artifact_state(data: dict[str, Any]) -> dict[str, Any]:
    base = data.get("base")
    batches = list_batch_refs()
    return {
        "base": base,
        "head": current_head(),
        "phase": data.get("phase"),
        "mode": data.get("mode"),
        "state_dir": str(STATE_DIR),
        "checkpoint": str(CHECKPOINT_PATH),
        "checkpoint_exists": CHECKPOINT_PATH.exists(),
        "plan_exists": PLAN_PATH.exists(),
        "candidate_exists": CANDIDATE_PATH.exists(),
        "narrative_exists": NARRATIVE_PATH.exists(),
        "plan_sha256": file_digest(PLAN_PATH),
        "candidate_sha256": file_digest(CANDIDATE_PATH),
        "narrative_sha256": file_digest(NARRATIVE_PATH),
        "batch_count": len(batches),
        "batches": batches,
        "completed_batches": data.get("completed_batches", []),
        "current_batch": data.get("current_batch"),
        "commits_recorded": data.get("commits", []),
        "commits_since_base": commit_count_since(base if isinstance(base, str) else None),
    }


def infer_resume_target(state: dict[str, Any]) -> str:
    if not state["checkpoint_exists"]:
        return "fresh"
    phase = state["phase"]
    if phase == "phase3-complete":
        return "complete"
    if phase == "phase1-running":
        return "phase1"
    if phase in {"phase3-running", "refine-history-running"}:
        if state["batch_count"]:
            return "phase3-after-gate2"
        return "gate3-or-manual-audit"
    if phase == "phase2-complete":
        return "phase3-after-gate2"
    if phase in {"phase1-complete", "phase2-running"}:
        return "phase2-after-gate1"
    if phase == "phase1-candidate":
        return "gate1"
    if state["batch_count"] and state["plan_exists"]:
        return "phase3-after-gate2"
    if state["plan_exists"]:
        return "phase2-after-gate1"
    if state["candidate_exists"] and state["narrative_exists"]:
        return "gate1"
    if state["candidate_exists"] or state["narrative_exists"]:
        return "phase1"
    if state["commits_since_base"]:
        return "gate3-or-manual-audit"
    return "fresh"


def save_checkpoint(data: dict[str, Any]) -> None:
    require_safe_state_dir()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now()
    data["artifacts"] = artifact_state(data)
    data["artifacts"]["checkpoint_exists"] = True
    payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        dir=STATE_DIR,
        prefix=f".{CHECKPOINT_PATH.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, CHECKPOINT_PATH)
        fsync_directory(STATE_DIR)
    except BaseException:
        temporary_path.unlink(missing_ok=True)
        raise


def clear_state_dir_for_fresh_start() -> list[str]:
    require_safe_state_dir()
    if not STATE_DIR.exists():
        return []
    removed: list[str] = []
    paths = [
        path
        for path in DECOMPOSE_STATE_PATHS
        if path.exists() or path.is_symlink()
    ]
    paths.extend(
        path
        for path in STATE_DIR.glob(f".{CHECKPOINT_PATH.name}.*.tmp")
        if path.is_file() or path.is_symlink()
    )
    for path in paths:
        removed.append(path.name)
        if path.is_symlink() or not path.is_dir():
            path.unlink()
        else:
            shutil.rmtree(path)
    return sorted(removed)


def cmd_start(args: argparse.Namespace) -> None:
    if args.mode == "resume":
        data = require_checkpoint()
        base = data["base"]
        if (
            args.base is not None
            and resolve_commit(args.base) != base
        ):
            raise SystemExit(
                "resume base does not match the decompose checkpoint"
            )
        previous_phase = data.get("phase")
        data["phase"] = "phase1-running"
        data["events"].append(
            {
                "at": now(),
                "event": "start",
                "mode": "resume",
                "base": base,
                "previous_phase": previous_phase,
                "cleared_state_files": [],
            }
        )
        save_checkpoint(data)
        print(str(CHECKPOINT_PATH))
        return

    base_revision = args.base or "HEAD"
    base = resolve_commit(base_revision)
    if not base:
        raise SystemExit(
            f"invalid decompose base revision: {base_revision}"
        )
    removed = clear_state_dir_for_fresh_start()
    data: dict[str, Any] = {
        "schema": 1,
        "created_at": now(),
        "mode": args.mode,
        "phase": "started",
        "base": base,
        "events": [],
        "completed_batches": [],
        "commits": [],
    }
    data["events"].append(
        {
            "at": now(),
            "event": "start",
            "mode": args.mode,
            "base": base,
            "cleared_state_files": removed,
        }
    )
    save_checkpoint(data)
    print(str(CHECKPOINT_PATH))


def cmd_mark(args: argparse.Namespace) -> None:
    data = load_checkpoint()
    if not data:
        data = {
            "schema": 1,
            "created_at": now(),
            "mode": "unknown",
            "base": current_head(),
            "events": [],
            "completed_batches": [],
            "commits": [],
        }
    if args.phase:
        data["phase"] = args.phase
    if args.current_batch:
        data["current_batch"] = args.current_batch
    completed = data.setdefault("completed_batches", [])
    if args.completed_batch and args.completed_batch not in completed:
        completed.append(args.completed_batch)
    if args.completed_batch and data.get("current_batch") == args.completed_batch:
        data.pop("current_batch", None)
    if args.commit:
        commit = args.commit
        if commit == "HEAD":
            commit = current_head()
        subject = git_output("log", "-1", "--format=%s", commit)
        commits = data.setdefault("commits", [])
        if not any(item.get("sha") == commit for item in commits if isinstance(item, dict)):
            commits.append({"sha": commit, "subject": subject})
    event: dict[str, Any] = {"at": now(), "event": "mark"}
    for key in ("phase", "current_batch", "completed_batch", "commit", "note"):
        value = getattr(args, key)
        if value:
            event[key] = value
    data.setdefault("events", []).append(event)
    save_checkpoint(data)
    print(str(CHECKPOINT_PATH))


def cmd_status(args: argparse.Namespace) -> None:
    data = load_checkpoint()
    state = artifact_state(data)
    state["resume_target"] = infer_resume_target(state)
    state["events"] = data.get("events", [])[-10:]
    if args.json:
        print(json.dumps(state, indent=2, sort_keys=True))
        return
    print(f"checkpoint: {state['checkpoint_exists']} {state['checkpoint']}")
    print(f"phase: {state['phase'] or 'unknown'}")
    print(f"resume_target: {state['resume_target']}")
    print(f"base: {state['base'] or 'unknown'}")
    print(f"head: {state['head'] or 'unknown'}")
    print(f"plan: {state['plan_exists']} candidate: {state['candidate_exists']} narrative: {state['narrative_exists']}")
    print(f"batches: {state['batch_count']}")
    if state["current_batch"]:
        print(f"current_batch: {state['current_batch']}")
    if state["completed_batches"]:
        print("completed_batches:")
        for batch in state["completed_batches"]:
            print(f"  {batch}")


def cmd_state_dir(_args: argparse.Namespace) -> None:
    require_safe_state_dir()
    print(STATE_DIR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    state_dir = sub.add_parser("state-dir")
    state_dir.set_defaults(func=cmd_state_dir)

    start = sub.add_parser("start")
    start.add_argument(
        "--mode",
        required=True,
        choices=["full", "deconstruct", "reconstruct", "resume"],
    )
    start.add_argument("--base")
    start.set_defaults(func=cmd_start)

    mark = sub.add_parser("mark")
    mark.add_argument("--phase")
    mark.add_argument("--current-batch")
    mark.add_argument("--completed-batch")
    mark.add_argument("--commit")
    mark.add_argument("--note")
    mark.set_defaults(func=cmd_mark)

    status = sub.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
