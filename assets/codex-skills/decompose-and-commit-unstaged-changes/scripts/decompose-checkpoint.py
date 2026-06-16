#!/usr/bin/env python3
"""Record and inspect resumable decompose workflow state."""

from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
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
PRESERVE_ON_FRESH_START = {".gitignore"}


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


def file_digest(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_checkpoint() -> dict[str, Any]:
    if not CHECKPOINT_PATH.exists():
        return {}
    try:
        data = json.loads(CHECKPOINT_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"checkpoint_error": "invalid json"}
    return data if isinstance(data, dict) else {}


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
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    data["updated_at"] = now()
    CHECKPOINT_PATH.touch(exist_ok=True)
    data["artifacts"] = artifact_state(data)
    CHECKPOINT_PATH.write_text(
        json.dumps(data, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def clear_state_dir_for_fresh_start() -> list[str]:
    if not STATE_DIR.exists():
        return []
    removed: list[str] = []
    for path in STATE_DIR.iterdir():
        if path.name in PRESERVE_ON_FRESH_START:
            continue
        removed.append(path.name)
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    return sorted(removed)


def cmd_start(args: argparse.Namespace) -> None:
    removed = [] if args.mode == "resume" else clear_state_dir_for_fresh_start()
    base = args.base or current_head()
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    state_dir = sub.add_parser("state-dir")
    state_dir.set_defaults(func=lambda args: print(STATE_DIR))

    start = sub.add_parser("start")
    start.add_argument(
        "--mode",
        required=True,
        choices=["full", "deconstruct", "reconstruct", "resume", "history-polish"],
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
