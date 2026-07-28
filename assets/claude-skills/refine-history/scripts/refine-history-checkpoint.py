#!/usr/bin/env python3
"""Validate, checkpoint, and audit a local history-refinement run."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


WEAK_KEEP_REASON = re.compile(
    r"\b(single behavior|same module|one module|same function|one function|"
    r"one entry point|single CLI entry point|fixture set|tests belong together|"
    r"tests for one module|tests for one function|coherent unit|shared helper|"
    r"large but related|single pipeline|one pipeline|same pipeline|"
    r"full pipeline|execution pipeline|artificial subdivision|"
    r"no meaningful subdivision|across its variants|all stages)\b",
    re.IGNORECASE,
)
NO_BREAKAGE = re.compile(r"\b(no|without)\s+(immediate\s+)?breakage\b", re.IGNORECASE)
MULTI_OUTCOME_SUBJECT = re.compile(
    r"(?:\band\b|\balso\b|\bas well as\b|;)", re.IGNORECASE
)
ACTION_SHAPED_SUBJECT = re.compile(
    r"\b(Add|Register|Cover|Scaffold|Invoke|Wire|Expand)\b",
    re.IGNORECASE,
)
REPAIR_PROCESS_MESSAGE = re.compile(
    r"\b(?:restore(?:s|d|ing)?|repair(?:s|ed|ing)?|recover(?:s|ed|ing)?|"
    r"compensat(?:e|es|ed|ing)|lost|missing|decomposition|batch(?:es)?|"
    r"fixup|cleanup|squash)\b",
    re.IGNORECASE,
)
FIX_SUBJECT = re.compile(r"^fix(?:\([^)]*\))?!?:", re.IGNORECASE)
ORCHESTRATION_PATH = re.compile(
    r"(^docs?/|README|examples/|tests?/|workflows?|cli|build|"
    r"pyproject|setup|Makefile)"
)


def git_run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "--no-optional-locks", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def git_output(*args: str) -> str:
    return git_run(*args).stdout.strip()


def default_state_dir() -> Path:
    try:
        root = git_output("rev-parse", "--show-toplevel")
    except subprocess.CalledProcessError:
        root = str(Path.cwd().resolve())
    return Path(root) / ".git-stage-batch" / "refine-history"


STATE_DIR = default_state_dir()
CHECKPOINT_PATH = STATE_DIR / "checkpoint.json"
PRESSURE_PATH = STATE_DIR / "pressure.json"
AUDIT_PATH = STATE_DIR / "audit.json"
VERIFICATION_PATH = STATE_DIR / "verification.json"
SNAPSHOT_HELPER = Path(__file__).with_name("verify-head-snapshot.py")
MESSAGE_REFINEMENT_DIR = STATE_DIR.parent / "refine-commit-messages"
MESSAGE_REFINEMENT_CHECKPOINT = MESSAGE_REFINEMENT_DIR / "checkpoint.json"


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def require_safe_state_dir() -> None:
    for path in (STATE_DIR.parent, STATE_DIR):
        if path.is_symlink():
            raise SystemExit(f"refusing to use symlinked state path: {path}")


def load_json(path: Path) -> dict[str, Any]:
    require_safe_state_dir()
    if path.is_symlink():
        raise SystemExit(f"refusing to read symlinked state file: {path}")
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"expected a JSON object in {path}")
    return value


def write_text(path: Path, text: str) -> None:
    require_safe_state_dir()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(text)
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def write_json(path: Path, data: dict[str, Any]) -> None:
    write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def load_checkpoint() -> dict[str, Any]:
    return load_json(CHECKPOINT_PATH)


def save_checkpoint(data: dict[str, Any]) -> None:
    require_safe_state_dir()
    data["updated_at"] = now()
    write_json(CHECKPOINT_PATH, data)


def clear_state_dir() -> None:
    require_safe_state_dir()
    if not STATE_DIR.exists():
        return
    for path in STATE_DIR.iterdir():
        if path.is_symlink():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


def canonical_base(revision: str) -> str:
    try:
        return git_output("rev-parse", "--verify", f"{revision}^{{commit}}")
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"invalid base revision {revision!r}") from error


def range_commits(
    base: str,
    tip: str = "HEAD",
    *,
    allow_empty: bool = False,
) -> list[str]:
    try:
        git_run("merge-base", "--is-ancestor", base, tip)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"base {base} is not an ancestor of {tip}") from error
    commits = git_output("rev-list", "--reverse", f"{base}..{tip}").splitlines()
    if not commits and not allow_empty:
        raise SystemExit("refine-history range is empty")
    merges = git_output("rev-list", "--merges", f"{base}..{tip}").splitlines()
    if merges:
        listed = "\n".join(f"  {commit}" for commit in merges)
        raise SystemExit(
            "refine-history supports only linear ranges; merge commits found:\n"
            f"{listed}"
        )
    return commits


def git_dir() -> Path:
    return Path(git_output("rev-parse", "--absolute-git-dir"))


def current_branch_ref() -> str | None:
    result = git_run("symbolic-ref", "-q", "HEAD", check=False)
    branch_ref = result.stdout.strip()
    return branch_ref or None


def active_rebase() -> tuple[bool, str | None]:
    for directory_name in ("rebase-merge", "rebase-apply"):
        directory = git_dir() / directory_name
        if not directory.exists():
            continue
        head_name = directory / "head-name"
        if not head_name.is_file():
            return True, None
        branch_ref = head_name.read_text(encoding="utf-8").strip()
        return True, branch_ref or None
    return False, None


def reject_non_rebase_operations() -> None:
    for marker_name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        if (git_dir() / marker_name).exists():
            raise SystemExit(f"refusing to run during Git operation: {marker_name}")


def require_clean_worktree() -> None:
    if git_run("diff", "--quiet", check=False).returncode:
        raise SystemExit("refine-history requires a clean working tree")
    if git_run("diff", "--cached", "--quiet", check=False).returncode:
        raise SystemExit("refine-history requires a clean index")
    untracked = git_run(
        "ls-files",
        "--others",
        "--exclude-standard",
        "-z",
    ).stdout.split("\x00")
    unexplained = [
        path
        for path in untracked
        if path
        and path != ".git-stage-batch"
        and not path.startswith(".git-stage-batch/")
    ]
    if unexplained:
        listed = "\n".join(f"  {path}" for path in unexplained)
        raise SystemExit(f"refine-history requires no untracked work:\n{listed}")


def remote_refs_containing(commit: str) -> list[str]:
    return git_output(
        "for-each-ref",
        "--format=%(refname)",
        "--contains",
        commit,
        "refs/remotes",
    ).splitlines()


def reject_shared_commits(commits: list[str]) -> None:
    shared: list[tuple[str, list[str]]] = []
    for commit in commits:
        refs = remote_refs_containing(commit)
        if refs:
            shared.append((commit, refs))
    if shared:
        details = "\n".join(f"  {commit}: {', '.join(refs)}" for commit, refs in shared)
        raise SystemExit(
            f"refusing to rewrite commits contained in remote-tracking refs:\n{details}"
        )


def validate_range(revision: str, *, reject_shared: bool) -> tuple[str, list[str]]:
    base = canonical_base(revision)
    commits = range_commits(base)
    if reject_shared:
        reject_shared_commits(commits)
    return base, commits


def commit_message(commit: str) -> tuple[str, str]:
    raw = git_output("show", "-s", "--format=%s%x00%b", commit)
    subject, _, body = raw.partition("\x00")
    return subject, body


def pressure_reasons(commit: str, subject: str, body: str) -> list[str]:
    stat = git_output("show", "--shortstat", "--format=", "--find-renames", commit)
    names = git_output(
        "show",
        "--name-only",
        "--format=",
        "--find-renames",
        commit,
    ).splitlines()
    added_numstat = git_output(
        "show",
        "--numstat",
        "--format=",
        "--diff-filter=A",
        "--find-renames",
        commit,
    ).splitlines()
    numbers: dict[str, int] = {}
    for key, pattern in (
        ("files", r"(\d+) files? changed"),
        ("insertions", r"(\d+) insertions?"),
        ("deletions", r"(\d+) deletions?"),
    ):
        match = re.search(pattern, stat)
        numbers[key] = int(match.group(1)) if match else 0

    reasons: list[str] = []
    changed_lines = numbers["insertions"] + numbers["deletions"]
    if changed_lines >= 500:
        reasons.append(f"{changed_lines} changed lines")
    if numbers["files"] >= 10:
        reasons.append(f"{numbers['files']} files")
    if ACTION_SHAPED_SUBJECT.search(subject):
        reasons.append("artifact/action-shaped subject")
    if MULTI_OUTCOME_SUBJECT.search(subject):
        reasons.append("multi-outcome subject")
    if any(ORCHESTRATION_PATH.search(path) for path in names):
        reasons.append("docs/tests/orchestration/build surface")
    for line in added_numstat:
        added, _, path = (line.split("\t", 2) + ["", ""])[:3]
        if added.isdigit() and int(added) >= 600:
            reasons.append(f"new 600+ line file: {path}")
    if REPAIR_PROCESS_MESSAGE.search(f"{subject}\n{body}") or FIX_SUBJECT.search(
        subject
    ):
        reasons.append("repair/process-shaped message")
    return reasons


def pressure_document(base: str, commits: list[str]) -> dict[str, Any]:
    entries = []
    for commit in commits:
        subject, body = commit_message(commit)
        entries.append(
            {
                "sha": commit,
                "subject": subject,
                "reasons": pressure_reasons(commit, subject, body),
            }
        )
    return {
        "schema": 1,
        "base": base,
        "head": git_output("rev-parse", "HEAD"),
        "commits": entries,
    }


def nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def nonempty_string_list(value: Any) -> bool:
    return (
        isinstance(value, list)
        and bool(value)
        and all(nonempty_string(item) for item in value)
    )


def validate_split_probes(value: Any, label: str, errors: list[str]) -> None:
    if not isinstance(value, list) or not value:
        errors.append(f"{label}: pressured KEEP requires split_probes")
        return
    for index, probe in enumerate(value, start=1):
        probe_label = f"{label} split probe {index}"
        if not isinstance(probe, dict):
            errors.append(f"{probe_label}: expected an object")
            continue
        if not nonempty_string(probe.get("candidate")):
            errors.append(f"{probe_label}: candidate is required")
        blocking_reason = probe.get("blocking_reason")
        if not nonempty_string(blocking_reason):
            errors.append(f"{probe_label}: blocking_reason is required")
            continue
        if NO_BREAKAGE.search(blocking_reason):
            errors.append(f"{probe_label}: no-breakage probes must be split")
        if WEAK_KEEP_REASON.search(blocking_reason):
            errors.append(f"{probe_label}: blocking_reason uses weak rationale")


def validate_audit(base: str, commits: list[str]) -> None:
    audit = load_json(AUDIT_PATH)
    pressure = pressure_document(base, commits)
    errors: list[str] = []

    if audit.get("schema") != 1:
        errors.append("audit schema must be 1")
    if audit.get("base") != base:
        errors.append("audit base does not match the canonical base")
    if audit.get("head") != pressure["head"]:
        errors.append("audit head does not match the current HEAD")

    audit_entries = audit.get("commits")
    if not isinstance(audit_entries, list):
        raise SystemExit("audit commits must be a list")
    expected_entries = pressure["commits"]
    actual_shas = [
        entry.get("sha") if isinstance(entry, dict) else None for entry in audit_entries
    ]
    expected_shas = [entry["sha"] for entry in expected_entries]
    if actual_shas != expected_shas:
        errors.append(
            "audit must cover every current commit exactly once in series order"
        )

    for index, expected in enumerate(expected_entries):
        if index >= len(audit_entries):
            break
        entry = audit_entries[index]
        if not isinstance(entry, dict):
            errors.append(f"commit {index + 1}: expected an object")
            continue
        label = expected["sha"][:12]
        if entry.get("subject") != expected["subject"]:
            errors.append(f"{label}: audit subject does not match the commit")
        if entry.get("verdict") != "KEEP":
            errors.append(f"{label}: final audit verdict must be KEEP")
        reason = entry.get("reason")
        if not nonempty_string(reason):
            errors.append(f"{label}: reason is required")
        elif WEAK_KEEP_REASON.search(reason):
            errors.append(f"{label}: reason uses weak KEEP rationale")

        reasons = expected["reasons"]
        if reasons:
            if entry.get("pressure") != reasons:
                errors.append(f"{label}: pressure reasons do not match pressure.json")
            if not nonempty_string(entry.get("smallest_runnable_spine")):
                errors.append(
                    f"{label}: pressured KEEP requires smallest_runnable_spine"
                )
            if not nonempty_string_list(entry.get("later_enrichments_checked")):
                errors.append(
                    f"{label}: pressured KEEP requires later_enrichments_checked"
                )
            validate_split_probes(entry.get("split_probes"), label, errors)
        if "repair/process-shaped message" in reasons and not nonempty_string(
            entry.get("repair_process_false_positive")
        ):
            errors.append(
                f"{label}: repair/process-shaped message must be integrated or "
                "documented as a concrete false positive"
            )
        if MULTI_OUTCOME_SUBJECT.search(expected["subject"]):
            errors.append(f"{label}: multi-outcome subject must be reworded or split")

    if errors:
        raise SystemExit("refine-history audit failed:\n" + "\n".join(errors))
    write_json(PRESSURE_PATH, pressure)
    print(f"audit valid for {len(commits)} commits")


def write_original_artifacts(base: str) -> dict[str, Any]:
    head = git_output("rev-parse", "HEAD")
    tree = git_output("rev-parse", "HEAD^{tree}")
    count = git_output("rev-list", "--count", f"{base}..HEAD")
    series = git_output("log", "--reverse", "--format=%H %s", f"{base}..HEAD")
    (STATE_DIR / "pre-tree.txt").write_text(tree + "\n", encoding="utf-8")
    (STATE_DIR / "pre-count.txt").write_text(count + "\n", encoding="utf-8")
    (STATE_DIR / "pre-series.txt").write_text(
        series + ("\n" if series else ""),
        encoding="utf-8",
    )
    return {
        "base": base,
        "original_head": head,
        "original_tree": tree,
        "original_count": int(count),
    }


def create_recovery_ref(head: str) -> str:
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"refs/refine-history/backups/{stamp}"
    suffix = 1
    while True:
        recovery_ref = prefix if suffix == 1 else f"{prefix}-{suffix}"
        result = git_run("update-ref", recovery_ref, head, "", check=False)
        if result.returncode == 0:
            return recovery_ref
        if git_run(
            "show-ref", "--verify", "--quiet", recovery_ref, check=False
        ).returncode:
            result.check_returncode()
        suffix += 1


def cmd_check_range(args: argparse.Namespace) -> None:
    base, _ = validate_range(args.base, reject_shared=True)
    print(base)


def cmd_start(args: argparse.Namespace) -> None:
    base, _ = validate_range(args.base, reject_shared=True)
    require_safe_state_dir()
    reject_non_rebase_operations()
    rebase_active, _ = active_rebase()
    if rebase_active:
        raise SystemExit("refusing to start refine-history during a rebase")
    require_clean_worktree()
    branch_ref = current_branch_ref()
    if branch_ref is None:
        raise SystemExit("refine-history requires a named local branch")
    original_head = git_output("rev-parse", "HEAD")
    recovery_ref = create_recovery_ref(original_head)
    clear_state_dir()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    original = write_original_artifacts(base)
    data: dict[str, Any] = {
        "schema": 1,
        "created_at": now(),
        "phase": "started",
        "branch_ref": branch_ref,
        "recovery_ref": recovery_ref,
        "events": [{"at": now(), "event": "start", "base": base}],
        **original,
    }
    save_checkpoint(data)
    print(str(CHECKPOINT_PATH))


def cmd_mark(args: argparse.Namespace) -> None:
    data = load_checkpoint()
    if not data:
        raise SystemExit("no refine-history checkpoint; run start first")
    if args.phase == "complete":
        raise SystemExit("use the complete command to mark refinement complete")
    if args.phase:
        data["phase"] = args.phase
    event = {"at": now(), "event": "mark"}
    if args.phase:
        event["phase"] = args.phase
    if args.note:
        event["note"] = args.note
    data.setdefault("events", []).append(event)
    save_checkpoint(data)
    print(str(CHECKPOINT_PATH))


def cmd_status(args: argparse.Namespace) -> None:
    data = load_checkpoint()
    state = {
        "checkpoint": str(CHECKPOINT_PATH),
        "checkpoint_exists": CHECKPOINT_PATH.exists(),
        "state_dir": str(STATE_DIR),
        **data,
    }
    if args.json:
        print(json.dumps(state, indent=2, sort_keys=True))
        return
    print(f"checkpoint: {state['checkpoint_exists']} {CHECKPOINT_PATH}")
    print(f"phase: {state.get('phase', 'unknown')}")
    print(f"base: {state.get('base', 'unknown')}")
    print(f"branch_ref: {state.get('branch_ref', 'unknown')}")
    print(f"original_head: {state.get('original_head', 'unknown')}")
    print(f"recovery_ref: {state.get('recovery_ref', 'unknown')}")


def cmd_base(_: argparse.Namespace) -> None:
    data = load_checkpoint()
    base = data.get("base")
    if not nonempty_string(base):
        raise SystemExit("no canonical base in refine-history checkpoint")
    print(base)


def cmd_recovery_ref(_: argparse.Namespace) -> None:
    data = load_checkpoint()
    recovery_ref = data.get("recovery_ref")
    if not nonempty_string(recovery_ref):
        raise SystemExit("no recovery ref in refine-history checkpoint")
    print(recovery_ref)


def state_text(path: Path) -> str:
    require_safe_state_dir()
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"missing or unsafe refine-history artifact: {path}")
    return path.read_text(encoding="utf-8").rstrip("\n")


def validate_resume_checkpoint() -> tuple[dict[str, Any], str, bool]:
    data = load_checkpoint()
    if data.get("schema") != 1:
        raise SystemExit("no valid refine-history checkpoint to resume")
    base = data.get("base")
    branch_ref = data.get("branch_ref")
    original_head = data.get("original_head")
    original_tree = data.get("original_tree")
    original_count = data.get("original_count")
    recovery_ref = data.get("recovery_ref")
    if not all(
        nonempty_string(value)
        for value in (base, branch_ref, original_head, original_tree, recovery_ref)
    ) or not isinstance(original_count, int):
        raise SystemExit("refine-history checkpoint is incomplete")
    assert isinstance(base, str)
    assert isinstance(branch_ref, str)
    assert isinstance(original_head, str)
    assert isinstance(original_tree, str)
    assert isinstance(recovery_ref, str)
    if canonical_base(base) != base:
        raise SystemExit("checkpoint base is not canonical")
    try:
        recovery_head = git_output(
            "rev-parse",
            "--verify",
            f"{recovery_ref}^{{commit}}",
        )
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"missing recovery ref: {recovery_ref}") from error
    original_commits = range_commits(base, recovery_ref)
    current_commits = range_commits(base, allow_empty=True)
    reject_shared_commits(list(dict.fromkeys([*original_commits, *current_commits])))
    reject_non_rebase_operations()
    rebase_active, rebase_branch_ref = active_rebase()
    if rebase_active:
        if rebase_branch_ref != branch_ref:
            raise SystemExit(
                f"active rebase does not belong to the checkpoint branch {branch_ref}"
            )
    elif current_branch_ref() != branch_ref:
        raise SystemExit(
            f"refusing to resume on a different branch; expected {branch_ref}"
        )
    expected_series = git_output(
        "log",
        "--reverse",
        "--format=%H %s",
        f"{base}..{recovery_ref}",
    )
    errors = []
    if recovery_head != original_head:
        errors.append("recovery ref does not point to original_head")
    if git_output("rev-parse", f"{recovery_ref}^{{tree}}") != original_tree:
        errors.append("recovery ref tree does not match original_tree")
    if len(original_commits) != original_count:
        errors.append("recovery range count does not match original_count")
    if state_text(STATE_DIR / "pre-tree.txt") != original_tree:
        errors.append("pre-tree.txt does not match the checkpoint")
    if state_text(STATE_DIR / "pre-count.txt") != str(original_count):
        errors.append("pre-count.txt does not match the checkpoint")
    if state_text(STATE_DIR / "pre-series.txt") != expected_series:
        errors.append("pre-series.txt does not match the recovery range")
    if not rebase_active and git_output("rev-parse", "HEAD^{tree}") != original_tree:
        errors.append("current branch tree does not match the preserved final tree")
    if errors:
        raise SystemExit("cannot resume refine-history:\n" + "\n".join(errors))
    return data, base, rebase_active


def cmd_check_resume(_: argparse.Namespace) -> None:
    _, base, _ = validate_resume_checkpoint()
    print(base)


def cmd_pressure(args: argparse.Namespace) -> None:
    base, commits = validate_range(args.base, reject_shared=False)
    document = pressure_document(base, commits)
    write_json(PRESSURE_PATH, document)
    print(str(PRESSURE_PATH))


def cmd_validate_audit(args: argparse.Namespace) -> None:
    base, commits = validate_range(args.base, reject_shared=False)
    validate_audit(base, commits)


def cmd_verify_range(args: argparse.Namespace) -> None:
    command = args.verification_command
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise SystemExit("verify-range requires a verification command after --")
    _, checkpoint_base, rebase_active = validate_resume_checkpoint()
    base, commits = validate_range(args.base, reject_shared=False)
    if base != checkpoint_base:
        raise SystemExit("verification base does not match the checkpoint")
    if rebase_active:
        raise SystemExit("cannot verify the final range during a rebase")
    for commit in commits:
        result = subprocess.run(
            [
                sys.executable,
                str(SNAPSHOT_HELPER),
                "--ref",
                commit,
                "--",
                *command,
            ],
            check=False,
        )
        if result.returncode:
            raise SystemExit(
                f"snapshot verification failed for {commit}: exit {result.returncode}"
            )
    write_json(
        VERIFICATION_PATH,
        {
            "schema": 1,
            "base": base,
            "head": git_output("rev-parse", "HEAD"),
            "commits": commits,
            "command": command,
            "verified_at": now(),
        },
    )
    print(str(VERIFICATION_PATH))


def validate_message_refinement_completion(base: str) -> None:
    if MESSAGE_REFINEMENT_DIR.is_symlink():
        raise SystemExit("refusing to use symlinked refine-commit-messages state path")
    if (
        MESSAGE_REFINEMENT_CHECKPOINT.is_symlink()
        or not MESSAGE_REFINEMENT_CHECKPOINT.is_file()
    ):
        raise SystemExit(
            "refine-history requires a completed refine-commit-messages run"
        )
    try:
        checkpoint = json.loads(
            MESSAGE_REFINEMENT_CHECKPOINT.read_text(encoding="utf-8")
        )
    except json.JSONDecodeError as error:
        raise SystemExit("invalid refine-commit-messages checkpoint JSON") from error
    if not isinstance(checkpoint, dict):
        raise SystemExit("invalid refine-commit-messages checkpoint")
    events = checkpoint.get("events")
    completion = events[-1] if isinstance(events, list) and events else None
    expected_branch = current_branch_ref()
    if (
        checkpoint.get("schema") != 1
        or checkpoint.get("phase") != "complete"
        or checkpoint.get("base") != base
        or checkpoint.get("branch_ref") != expected_branch
        or not isinstance(completion, dict)
        or completion.get("event") != "complete"
        or completion.get("head") != git_output("rev-parse", "HEAD")
    ):
        raise SystemExit(
            "refine-commit-messages completion does not match the current "
            "base, branch, and HEAD"
        )


def cmd_complete(args: argparse.Namespace) -> None:
    data, checkpoint_base, rebase_active = validate_resume_checkpoint()
    base, commits = validate_range(args.base, reject_shared=True)
    if base != checkpoint_base:
        raise SystemExit("completion base does not match the checkpoint")
    if rebase_active:
        raise SystemExit("cannot complete refine-history during a rebase")
    require_clean_worktree()
    validate_message_refinement_completion(base)
    validate_audit(base, commits)
    verification = load_json(VERIFICATION_PATH)
    if (
        verification.get("schema") != 1
        or verification.get("base") != base
        or verification.get("head") != git_output("rev-parse", "HEAD")
        or verification.get("commits") != commits
        or not nonempty_string_list(verification.get("command"))
    ):
        raise SystemExit("current commit range lacks a successful verify-range record")
    original_tree = data["original_tree"]
    if git_output("rev-parse", "HEAD^{tree}") != original_tree:
        raise SystemExit("final HEAD tree does not match the preserved original tree")
    post_series = git_output(
        "log",
        "--reverse",
        "--format=%H %s",
        f"{base}..HEAD",
    )
    write_text(STATE_DIR / "post-count.txt", f"{len(commits)}\n")
    write_text(
        STATE_DIR / "post-series.txt",
        post_series + ("\n" if post_series else ""),
    )
    data["phase"] = "complete"
    data.setdefault("events", []).append(
        {
            "at": now(),
            "event": "complete",
            "head": git_output("rev-parse", "HEAD"),
            "commit_count": len(commits),
        }
    )
    save_checkpoint(data)
    print(str(CHECKPOINT_PATH))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    state_dir = commands.add_parser("state-dir")
    state_dir.set_defaults(func=lambda args: print(STATE_DIR))

    check_range = commands.add_parser("check-range")
    check_range.add_argument("--base", required=True)
    check_range.set_defaults(func=cmd_check_range)

    start = commands.add_parser("start")
    start.add_argument("--base", required=True)
    start.set_defaults(func=cmd_start)

    mark = commands.add_parser("mark")
    mark.add_argument("--phase")
    mark.add_argument("--note")
    mark.set_defaults(func=cmd_mark)

    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    base = commands.add_parser("base")
    base.set_defaults(func=cmd_base)

    recovery_ref = commands.add_parser("recovery-ref")
    recovery_ref.set_defaults(func=cmd_recovery_ref)

    check_resume = commands.add_parser("check-resume")
    check_resume.set_defaults(func=cmd_check_resume)

    pressure = commands.add_parser("pressure")
    pressure.add_argument("--base", required=True)
    pressure.set_defaults(func=cmd_pressure)

    validate_audit_parser = commands.add_parser("validate-audit")
    validate_audit_parser.add_argument("--base", required=True)
    validate_audit_parser.set_defaults(func=cmd_validate_audit)

    verify_range = commands.add_parser("verify-range")
    verify_range.add_argument("--base", required=True)
    verify_range.add_argument("verification_command", nargs=argparse.REMAINDER)
    verify_range.set_defaults(func=cmd_verify_range)

    complete = commands.add_parser("complete")
    complete.add_argument("--base", required=True)
    complete.set_defaults(func=cmd_complete)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
