#!/usr/bin/env python3
"""Checkpoint and verify a message-only commit-series rewrite."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PROCESS_LANGUAGE = re.compile(
    r"\b(?:fixup|squash|rebase|reword|split|cleanup|decomposition|"
    r"reconstruction)\b",
    re.IGNORECASE,
)
MULTI_OUTCOME_SUBJECT = re.compile(
    r"(?:\band\b|\balso\b|\bas well as\b|;)",
    re.IGNORECASE,
)
FIRST_PARAGRAPH_ACTION = re.compile(
    r"^(?:this commit\b|add(?:s|ed|ing)?\b|introduc(?:e|es|ed|ing)\b|"
    r"implement(?:s|ed|ing)?\b|chang(?:e|es|ed|ing)\b|"
    r"updat(?:e|es|ed|ing)\b)",
    re.IGNORECASE,
)
FUTURE_TRANSITION = re.compile(
    r"\b(?:will|subsequent|next|later|following|future)\b",
    re.IGNORECASE,
)
VAGUE_FUTURE_TRANSITION = re.compile(
    r"^\s*(?:(?:more|additional|further|follow-up)\s+)?"
    r"(?:work|changes?)\s+(?:will\s+)?(?:follow|continue|remain)\b[.!]?\s*$",
    re.IGNORECASE,
)
FINAL_CONCLUSION = re.compile(
    r"\b(?:complet\w*|conclud\w*|finish\w*|now|together|result\w*|"
    r"series|goal|fully|overall)\b",
    re.IGNORECASE,
)
SERIES_PROGRESSION = {
    "opening": re.compile(r"\b(?:begin\w*|start\w*|lay\w*)\b", re.IGNORECASE),
    "opening-penultimate": re.compile(
        r"\b(?:begin\w*|start\w*|lay\w*)\b",
        re.IGNORECASE,
    ),
    "middle": re.compile(
        r"\b(?:continu\w*|advanc\w*|extend\w*)\b",
        re.IGNORECASE,
    ),
    "penultimate": re.compile(
        r"\b(?:continu\w*|advanc\w*|extend\w*)\b",
        re.IGNORECASE,
    ),
    "final": re.compile(
        r"\b(?:complet\w*|conclud\w*|finish\w*)\b",
        re.IGNORECASE,
    ),
}


def git_run(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run Git without optional locks."""
    return subprocess.run(
        ["git", "--no-optional-locks", *args],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def git_output(*args: str) -> str:
    """Return stripped Git stdout."""
    return git_run(*args).stdout.strip()


def repository_root() -> Path:
    """Return the repository root or the resolved current directory."""
    try:
        return Path(git_output("rev-parse", "--show-toplevel"))
    except subprocess.CalledProcessError:
        return Path.cwd().resolve()


STATE_DIR = repository_root() / ".git-stage-batch" / "refine-commit-messages"
CHECKPOINT_PATH = STATE_DIR / "checkpoint.json"
PRE_COMMITS_PATH = STATE_DIR / "pre-commits.json"
SCAN_PATH = STATE_DIR / "scan.json"
AUDIT_PATH = STATE_DIR / "audit.json"
REWRITE_PLAN_PATH = STATE_DIR / "rewrite-plan.json"


def now() -> str:
    """Return an ISO UTC timestamp."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def require_safe_state_dir() -> None:
    """Reject state paths that could escape through symlinks."""
    for path in (STATE_DIR.parent, STATE_DIR):
        if path.is_symlink():
            raise SystemExit(f"refusing to use symlinked state path: {path}")


def load_json(path: Path) -> dict[str, Any]:
    """Load one JSON object without following a state-file symlink."""
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
    """Atomically write one state file."""
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


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically write one JSON object."""
    write_text(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def clear_state_dir() -> None:
    """Clear only this skill's validated state directory."""
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


def load_checkpoint() -> dict[str, Any]:
    """Load the active refinement checkpoint."""
    return load_json(CHECKPOINT_PATH)


def save_checkpoint(data: dict[str, Any]) -> None:
    """Save the active refinement checkpoint."""
    data["updated_at"] = now()
    write_json(CHECKPOINT_PATH, data)


def canonical_base(revision: str) -> str:
    """Resolve a base expression once to a full commit ID."""
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
    """Return a non-merge range in oldest-first order."""
    try:
        git_run("merge-base", "--is-ancestor", base, tip)
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"base {base} is not an ancestor of {tip}") from error
    commits = git_output("rev-list", "--reverse", f"{base}..{tip}").splitlines()
    if not commits and not allow_empty:
        raise SystemExit("refine-commit-messages range is empty")
    merges = git_output("rev-list", "--merges", f"{base}..{tip}").splitlines()
    if merges:
        details = "\n".join(f"  {commit}" for commit in merges)
        raise SystemExit(
            "refine-commit-messages supports only linear ranges; "
            f"merge commits found:\n{details}"
        )
    return commits


def git_dir() -> Path:
    """Return this worktree's absolute Git directory."""
    return Path(git_output("rev-parse", "--absolute-git-dir"))


def rewrite_helper_snapshot_path() -> Path:
    """Return the Git-private path for the active rebase helper copy."""
    return (
        git_dir()
        / "git-stage-batch"
        / "refine-commit-messages"
        / "rewrite-helper.py"
    )


def snapshot_rewrite_helper() -> Path:
    """Freeze this helper outside the tree that the rebase will check out."""
    target = rewrite_helper_snapshot_path()
    for directory in (target.parent.parent, target.parent):
        if directory.is_symlink() or (
            directory.exists() and not directory.is_dir()
        ):
            raise SystemExit(
                f"refusing to use unsafe rewrite-helper directory: {directory}"
            )
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink() or (target.exists() and not target.is_file()):
        raise SystemExit(f"refusing to replace unsafe rewrite helper: {target}")

    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "wb",
            dir=target.parent,
            prefix=f".{target.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(Path(__file__).resolve(strict=True).read_bytes())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, target)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)
    return target.resolve(strict=True)


def current_branch_ref() -> str | None:
    """Return the current symbolic branch ref."""
    result = git_run("symbolic-ref", "-q", "HEAD", check=False)
    return result.stdout.strip() or None


def active_rebase() -> tuple[bool, str | None]:
    """Return whether a rebase is active and its original branch ref."""
    for directory_name in ("rebase-merge", "rebase-apply"):
        directory = git_dir() / directory_name
        if not directory.exists():
            continue
        head_name = directory / "head-name"
        if not head_name.is_file():
            return True, None
        return True, head_name.read_text(encoding="utf-8").strip() or None
    return False, None


def latest_rebase_pick() -> str | None:
    """Return the most recent original commit named in the active rebase log."""
    for directory_name in ("rebase-merge", "rebase-apply"):
        done_path = git_dir() / directory_name / "done"
        if not done_path.is_file() or done_path.is_symlink():
            continue
        with done_path.open("rb") as done_file:
            remaining = done_path.stat().st_size
            partial = b""
            while remaining:
                block_size = min(8192, remaining)
                remaining -= block_size
                done_file.seek(remaining)
                lines = (done_file.read(block_size) + partial).split(b"\n")
                partial = lines[0]
                for raw_line in reversed(lines[1:]):
                    stripped = raw_line.lstrip()
                    if stripped.startswith(b"pick "):
                        commit = stripped.split(maxsplit=2)[1].decode(
                            "ascii",
                            errors="strict",
                        )
                        return canonical_base(commit)
            stripped = partial.lstrip()
            if stripped.startswith(b"pick "):
                commit = stripped.split(maxsplit=2)[1].decode(
                    "ascii",
                    errors="strict",
                )
                return canonical_base(commit)
    return None


def reject_non_rebase_operations() -> None:
    """Reject operations outside the supported reword rebase."""
    for marker_name in ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD"):
        if (git_dir() / marker_name).exists():
            raise SystemExit(f"refusing to run during Git operation: {marker_name}")


def require_stable_head() -> None:
    """Reject an audit of a transient Git-operation state."""
    reject_non_rebase_operations()
    rebase_active, _ = active_rebase()
    if rebase_active:
        raise SystemExit("refusing to audit commit messages during a rebase")


def require_clean_worktree() -> None:
    """Require no tracked, staged, or unrelated untracked work."""
    if git_run("diff", "--quiet", check=False).returncode:
        raise SystemExit("refine-commit-messages requires a clean working tree")
    if git_run("diff", "--cached", "--quiet", check=False).returncode:
        raise SystemExit("refine-commit-messages requires a clean index")
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
        details = "\n".join(f"  {path}" for path in unexplained)
        raise SystemExit(
            "refine-commit-messages requires no untracked work:\n" + details
        )


def remote_refs_containing(commit: str) -> list[str]:
    """Return local remote-tracking refs containing a commit."""
    return git_output(
        "for-each-ref",
        "--format=%(refname)",
        "--contains",
        commit,
        "refs/remotes",
    ).splitlines()


def reject_shared_commits(commits: list[str]) -> None:
    """Reject one oldest-first linear chain visible through a remote ref."""
    if not commits:
        return
    oldest = commits[0]
    refs = remote_refs_containing(oldest)
    if not refs:
        return
    raise SystemExit(
        "refusing to rewrite a commit range contained in remote-tracking refs:\n"
        f"  {oldest}: {', '.join(refs)}"
    )


def protected_local_refs(
    commits: list[str],
    current_branch: str,
) -> dict[str, str]:
    """Record other local branches that point into the rewrite range."""
    commit_set = set(commits)
    refs: dict[str, str] = {}
    output = git_run(
        "for-each-ref",
        "--format=%(refname)%00%(objectname)",
        "refs/heads",
    ).stdout
    for line in output.splitlines():
        refname, separator, objectname = line.partition("\x00")
        if separator and refname != current_branch and objectname in commit_set:
            refs[refname] = objectname
    return dict(sorted(refs.items()))


def validate_range(revision: str, *, reject_shared: bool) -> tuple[str, list[str]]:
    """Resolve and validate one explicit history range."""
    base = canonical_base(revision)
    commits = range_commits(base)
    if reject_shared:
        reject_shared_commits(commits)
    return base, commits


def raw_commit(commit: str) -> tuple[list[str], str]:
    """Return raw headers and normalized message content."""
    raw = git_run("cat-file", "commit", commit).stdout
    headers_text, separator, message = raw.partition("\n\n")
    if not separator:
        raise SystemExit(f"cannot parse commit object {commit}")
    return headers_text.splitlines(), message.rstrip("\n")


def header_value(headers: list[str], name: str) -> str:
    """Return one required single-line commit header."""
    prefix = name + " "
    for line in headers:
        if line.startswith(prefix):
            return line[len(prefix) :]
    raise SystemExit(f"commit lacks required {name} header")


def message_parts(message: str) -> tuple[str, str, list[str], bool]:
    """Split a commit message into subject, body, and body paragraphs."""
    lines = message.splitlines()
    if not lines:
        return "", "", [], True
    subject = lines[0]
    separated = len(lines) == 1 or (len(lines) > 1 and not lines[1].strip())
    body_lines = lines[2:] if separated and len(lines) > 1 else lines[1:]
    body = "\n".join(body_lines).strip()
    paragraphs = (
        [
            paragraph.strip()
            for paragraph in re.split(r"\n[ \t]*\n", body)
            if paragraph.strip()
        ]
        if body
        else []
    )
    return subject, body, paragraphs, separated


def series_role(position: int, count: int) -> str:
    """Return the narrative role of a one-based series position."""
    if count == 1:
        return "single"
    if position == count:
        return "final"
    if count == 2 and position == 1:
        return "opening-penultimate"
    if position == count - 1:
        return "penultimate"
    if position == 1:
        return "opening"
    return "middle"


def message_signals(message: str, position: int, count: int) -> list[str]:
    """Return mechanical fallback-guideline pressure for one message."""
    subject, body, paragraphs, separated = message_parts(message)
    role = series_role(position, count)
    signals: list[str] = []
    if not subject.strip():
        signals.append("empty subject")
    if body and not separated:
        signals.append("missing blank line between subject and body")
    expected = 4 if count > 1 else 3
    if len(paragraphs) != expected:
        signals.append(
            f"expected {expected} body paragraphs for a {role} commit, "
            f"found {len(paragraphs)}"
        )
    for line_number, line in enumerate(body.splitlines(), start=1):
        if len(line) > 75:
            signals.append(
                f"body line {line_number} exceeds 75 characters ({len(line)})"
            )
    if paragraphs:
        first = paragraphs[0]
        if FIRST_PARAGRAPH_ACTION.search(first):
            signals.append(
                "first body paragraph describes the change instead of selected state"
            )
        if re.search(r"\b(?:but|however)\b", first, re.IGNORECASE):
            signals.append("first body paragraph merges selected state with a contrast")
    if len(paragraphs) >= 3:
        third = paragraphs[2]
        if not re.match(r"^This commit\b", third):
            signals.append("third body paragraph does not begin with 'This commit'")
        progression = SERIES_PROGRESSION.get(role)
        if progression is not None and not progression.search(third):
            signals.append(f"third body paragraph lacks {role} series progression")
    if count > 1 and len(paragraphs) >= 4:
        fourth = paragraphs[3]
        if role in {"penultimate", "opening-penultimate"}:
            if not re.search(r"\bthe final commit\b", fourth, re.IGNORECASE):
                signals.append(
                    "penultimate fourth paragraph does not name the final commit"
                )
            if re.search(r"\bsubsequent commits\b", fourth, re.IGNORECASE):
                signals.append(
                    "penultimate fourth paragraph refers to subsequent commits"
                )
        elif role in {"opening", "middle"}:
            if not FUTURE_TRANSITION.search(fourth):
                signals.append(f"{role} fourth paragraph does not identify future work")
            elif VAGUE_FUTURE_TRANSITION.search(fourth):
                signals.append(
                    f"{role} fourth paragraph describes future work only vaguely"
                )
        elif role == "final":
            if FUTURE_TRANSITION.search(fourth):
                signals.append("final fourth paragraph promises future work")
            if not FINAL_CONCLUSION.search(fourth):
                signals.append(
                    "final fourth paragraph does not clearly conclude the series"
                )
    if MULTI_OUTCOME_SUBJECT.search(subject):
        signals.append("subject may name multiple outcomes")
    if PROCESS_LANGUAGE.search(message):
        signals.append("message uses history-reconstruction language")
    return signals


def commit_record(commit: str, position: int, count: int) -> dict[str, Any]:
    """Return immutable metadata and message analysis for one commit."""
    headers, message = raw_commit(commit)
    subject, body, paragraphs, _ = message_parts(message)
    return {
        "position": position,
        "sha": commit,
        "tree": header_value(headers, "tree"),
        "author": header_value(headers, "author"),
        "signed": any(line.startswith("gpgsig ") for line in headers),
        "subject": subject,
        "body": body,
        "message": message,
        "paragraphs": paragraphs,
        "series_role": series_role(position, count),
        "signals": message_signals(message, position, count),
    }


def records_for(base: str, tip: str = "HEAD") -> list[dict[str, Any]]:
    """Return all range records in series order."""
    commits = range_commits(base, tip)
    count = len(commits)
    return [
        commit_record(commit, position, count)
        for position, commit in enumerate(commits, start=1)
    ]


def immutable_record(record: dict[str, Any]) -> dict[str, Any]:
    """Select metadata that a message-only rewrite must preserve."""
    return {
        key: record[key]
        for key in (
            "position",
            "sha",
            "tree",
            "author",
            "signed",
            "subject",
            "message",
        )
    }


def scan_document(base: str, *, mode: str) -> dict[str, Any]:
    """Build the structured message scan for the current range."""
    records = records_for(base)
    return {
        "schema": 1,
        "mode": mode,
        "base": base,
        "head": git_output("rev-parse", "HEAD"),
        "commit_count": len(records),
        "commits": records,
    }


def create_recovery_ref(head: str) -> str:
    """Create an atomic, collision-safe recovery ref."""
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    prefix = f"refs/refine-commit-messages/backups/{stamp}"
    suffix = 1
    while True:
        recovery_ref = prefix if suffix == 1 else f"{prefix}-{suffix}"
        result = git_run("update-ref", recovery_ref, head, "", check=False)
        if result.returncode == 0:
            return recovery_ref
        missing = git_run(
            "show-ref",
            "--verify",
            "--quiet",
            recovery_ref,
            check=False,
        ).returncode
        if missing:
            result.check_returncode()
        suffix += 1


def original_document(base: str, recovery_ref: str) -> dict[str, Any]:
    """Build the immutable pre-rewrite record."""
    records = records_for(base, recovery_ref)
    return {
        "schema": 1,
        "base": base,
        "head": git_output("rev-parse", recovery_ref),
        "commits": [immutable_record(record) for record in records],
    }


def compare_invariants(
    originals: list[dict[str, Any]],
    current: list[dict[str, Any]],
    *,
    include_signature: bool = True,
) -> list[str]:
    """Compare a current range to its message-only invariants."""
    errors: list[str] = []
    if len(current) != len(originals):
        return [
            f"commit count changed: expected {len(originals)}, found {len(current)}"
        ]
    for original, candidate in zip(originals, current, strict=True):
        position = original["position"]
        fields = ("tree", "author", "signed") if include_signature else ("tree", "author")
        for field in fields:
            if candidate.get(field) != original.get(field):
                errors.append(
                    f"position {position}: {field} changed during message refinement"
                )
    return errors


def validate_checkpoint(
    *,
    require_current: bool,
) -> tuple[dict[str, Any], str, bool, list[dict[str, Any]]]:
    """Validate checkpoint ownership, recovery data, and current invariants."""
    data = load_checkpoint()
    pre = load_json(PRE_COMMITS_PATH)
    if data.get("schema") != 1 or pre.get("schema") != 1:
        raise SystemExit("no valid refine-commit-messages checkpoint to resume")
    base = data.get("base")
    branch_ref = data.get("branch_ref")
    original_head = data.get("original_head")
    recovery_ref = data.get("recovery_ref")
    original_count = data.get("original_count")
    protected_refs = data.get("protected_refs")
    originals = pre.get("commits")
    if (
        not all(
            isinstance(value, str) and value
            for value in (base, branch_ref, original_head, recovery_ref)
        )
        or not isinstance(original_count, int)
        or not isinstance(protected_refs, dict)
        or not all(
            isinstance(refname, str)
            and refname.startswith("refs/heads/")
            and isinstance(objectname, str)
            and objectname
            for refname, objectname in protected_refs.items()
        )
        or not isinstance(originals, list)
    ):
        raise SystemExit("refine-commit-messages checkpoint is incomplete")
    assert isinstance(base, str)
    assert isinstance(branch_ref, str)
    assert isinstance(original_head, str)
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
    expected_pre = original_document(base, recovery_ref)
    errors: list[str] = []
    if recovery_head != original_head:
        errors.append("recovery ref does not point to original_head")
    if pre != expected_pre:
        errors.append("pre-commits.json does not match the recovery range")
    if len(originals) != original_count:
        errors.append("original commit count does not match pre-commits.json")
    for refname, objectname in protected_refs.items():
        result = git_run("rev-parse", "--verify", refname, check=False)
        if result.returncode or result.stdout.strip() != objectname:
            errors.append(
                f"out-of-scope local branch changed during refinement: {refname}"
            )
    reject_non_rebase_operations()
    rebase_active, rebase_branch = active_rebase()
    if rebase_active:
        if rebase_branch != branch_ref:
            errors.append(
                f"active rebase does not belong to checkpoint branch {branch_ref}"
            )
    elif current_branch_ref() != branch_ref:
        errors.append(
            f"refusing to resume on a different branch; expected {branch_ref}"
        )
    original_shas = [record["sha"] for record in originals if "sha" in record]
    current_shas = range_commits(base, allow_empty=True)
    reject_shared_commits(original_shas)
    if current_shas != original_shas:
        reject_shared_commits(current_shas)
    if require_current and not rebase_active:
        current = records_for(base)
        errors.extend(compare_invariants(originals, current))
    if errors:
        raise SystemExit("cannot resume refine-commit-messages:\n" + "\n".join(errors))
    return data, base, rebase_active, originals


def nonempty_string(value: Any) -> bool:
    """Return whether a value is a nonempty string."""
    return isinstance(value, str) and bool(value.strip())


def false_positive_map(
    value: Any,
    label: str,
    errors: list[str],
    allowed_sources: set[str],
) -> dict[str, str]:
    """Validate signal-false-positive entries and return them by signal."""
    if value is None:
        return {}
    if not isinstance(value, list):
        errors.append(f"{label}: signal_false_positives must be a list")
        return {}
    result: dict[str, str] = {}
    for index, entry in enumerate(value, start=1):
        if not isinstance(entry, dict):
            errors.append(f"{label}: false positive {index} must be an object")
            continue
        signal = entry.get("signal")
        source = entry.get("source")
        reason = entry.get("reason")
        if (
            not nonempty_string(signal)
            or not nonempty_string(source)
            or not nonempty_string(reason)
        ):
            errors.append(
                f"{label}: false positive {index} requires signal, source, and reason"
            )
            continue
        assert isinstance(signal, str)
        assert isinstance(source, str)
        assert isinstance(reason, str)
        if source not in allowed_sources:
            errors.append(
                f"{label}: false positive {index} cites an unknown source {source!r}"
            )
        if signal in result:
            errors.append(f"{label}: duplicate false positive for {signal!r}")
        result[signal] = reason
    return result


def proposed_message_signals(
    message: str,
    position: int,
    count: int,
) -> list[str]:
    """Analyze a proposed full replacement message."""
    return message_signals(message.rstrip("\n"), position, count)


def validate_audit_document(
    audit: dict[str, Any],
    scan: dict[str, Any],
    *,
    allow_reword: bool,
    expected_mode: str,
) -> None:
    """Validate exact audit coverage and signal dispositions."""
    errors: list[str] = []
    for field in ("schema", "mode", "base", "head"):
        expected = scan[field]
        if audit.get(field) != expected:
            errors.append(f"audit {field} does not match the current scan")
    if expected_mode != scan["mode"]:
        errors.append(f"scan mode must be {expected_mode}")
    conventions = audit.get("conventions")
    convention_sources: set[str] = set()
    if not isinstance(conventions, dict):
        errors.append("audit conventions must be an object")
    else:
        sources = conventions.get("sources")
        if (
            not isinstance(sources, list)
            or not sources
            or not all(nonempty_string(source) for source in sources)
        ):
            errors.append("audit conventions.sources must be a nonempty string list")
        else:
            convention_sources = set(sources)
        if not nonempty_string(conventions.get("summary")):
            errors.append("audit conventions.summary is required")
    entries = audit.get("commits")
    expected_entries = scan["commits"]
    if not isinstance(entries, list):
        raise SystemExit("audit commits must be a list")
    actual_shas = [
        entry.get("sha") if isinstance(entry, dict) else None for entry in entries
    ]
    expected_shas = [entry["sha"] for entry in expected_entries]
    if actual_shas != expected_shas:
        errors.append("audit must cover every current commit once in series order")
    count = len(expected_entries)
    for index, expected in enumerate(expected_entries):
        if index >= len(entries):
            break
        entry = entries[index]
        label = f"position {index + 1} ({expected['sha'][:12]})"
        if not isinstance(entry, dict):
            errors.append(f"{label}: audit entry must be an object")
            continue
        if entry.get("subject") != expected["subject"]:
            errors.append(f"{label}: subject does not match the current commit")
        if entry.get("signals") != expected["signals"]:
            errors.append(f"{label}: signals must match scan.json exactly")
        for field in ("reason", "patch_fidelity"):
            if not nonempty_string(entry.get(field)):
                errors.append(f"{label}: {field} is required")
        if count > 1 and not nonempty_string(entry.get("series_transition")):
            errors.append(f"{label}: series_transition is required")
        verdict = entry.get("verdict")
        if verdict not in {"KEEP", "REWORD"}:
            errors.append(f"{label}: verdict must be KEEP or REWORD")
            continue
        if verdict == "REWORD":
            if not allow_reword:
                errors.append(f"{label}: final audit verdict must be KEEP")
                continue
            proposed = entry.get("proposed_message")
            if not nonempty_string(proposed):
                errors.append(f"{label}: REWORD requires proposed_message")
                continue
            assert isinstance(proposed, str)
            if proposed.rstrip("\n") == expected["message"]:
                errors.append(f"{label}: proposed_message is unchanged")
            proposed_signals = proposed_message_signals(
                proposed,
                index + 1,
                count,
            )
            proposed_false_positives = false_positive_map(
                entry.get("proposed_signal_false_positives"),
                f"{label} proposed message",
                errors,
                convention_sources,
            )
            missing = [
                signal
                for signal in proposed_signals
                if signal not in proposed_false_positives
            ]
            extra = [
                signal
                for signal in proposed_false_positives
                if signal not in proposed_signals
            ]
            if missing:
                errors.append(
                    f"{label}: proposed message leaves unexplained signals: "
                    + "; ".join(missing)
                )
            if extra:
                errors.append(
                    f"{label}: proposed false positives are stale: " + "; ".join(extra)
                )
        else:
            false_positives = false_positive_map(
                entry.get("signal_false_positives"),
                label,
                errors,
                convention_sources,
            )
            missing = [
                signal
                for signal in expected["signals"]
                if signal not in false_positives
            ]
            extra = [
                signal
                for signal in false_positives
                if signal not in expected["signals"]
            ]
            if missing:
                errors.append(
                    f"{label}: KEEP leaves unexplained signals: " + "; ".join(missing)
                )
            if extra:
                errors.append(
                    f"{label}: signal false positives are stale: " + "; ".join(extra)
                )
    if errors:
        raise SystemExit("refine-commit-messages audit failed:\n" + "\n".join(errors))


def build_rewrite_plan(
    audit: dict[str, Any],
    scan: dict[str, Any],
) -> dict[str, Any]:
    """Freeze one validated audit as a position-indexed rewrite plan."""
    reword_positions = [
        position
        for position, entry in enumerate(audit["commits"], start=1)
        if entry["verdict"] == "REWORD"
    ]
    return {
        "schema": 1,
        "base": scan["base"],
        "source_head": scan["head"],
        "scan": scan,
        "audit": audit,
        "reword_positions": reword_positions,
    }


def load_rewrite_plan() -> dict[str, Any]:
    """Load and validate the frozen audit used by the one-pass rewrite."""
    plan = load_json(REWRITE_PLAN_PATH)
    scan = plan.get("scan")
    audit = plan.get("audit")
    positions = plan.get("reword_positions")
    if (
        plan.get("schema") != 1
        or not isinstance(scan, dict)
        or not isinstance(audit, dict)
        or not isinstance(positions, list)
        or not all(isinstance(position, int) for position in positions)
    ):
        raise SystemExit("invalid refine-commit-messages rewrite plan")
    if (
        plan.get("base") != scan.get("base")
        or plan.get("source_head") != scan.get("head")
        or scan.get("mode") != "refine"
    ):
        raise SystemExit("rewrite plan does not match its source scan")
    validate_audit_document(
        audit,
        scan,
        allow_reword=True,
        expected_mode="refine",
    )
    expected_positions = [
        position
        for position, entry in enumerate(audit["commits"], start=1)
        if entry["verdict"] == "REWORD"
    ]
    if positions != expected_positions or not positions:
        raise SystemExit("rewrite plan must contain every REWORD position in order")
    return plan


def validate_rewrite_plan_binding(
    plan: dict[str, Any],
    checkpoint: dict[str, Any],
    checkpoint_base: str,
) -> None:
    """Require the frozen plan to match the active checkpoint attempt."""
    if (
        plan["base"] != checkpoint_base
        or checkpoint.get("rewrite_source_head") != plan["source_head"]
        or checkpoint.get("rewrite_positions") != plan["reword_positions"]
    ):
        raise SystemExit("rewrite plan does not match the active checkpoint attempt")


def require_rewrite_helper(checkpoint: dict[str, Any]) -> None:
    """Require an internal rebase callback to use the frozen helper copy."""
    configured = checkpoint.get("rewrite_helper")
    try:
        expected = rewrite_helper_snapshot_path().resolve(strict=True)
        current = Path(__file__).resolve(strict=True)
    except OSError as error:
        raise SystemExit(
            "cannot resolve the checkpoint rewrite-helper snapshot"
        ) from error
    if configured != str(expected) or current != expected:
        raise SystemExit(
            "rewrite callback is not running from the checkpoint helper snapshot"
        )


def rewrite_entry_path(position: int) -> Path:
    """Return the state path for one constant-size rewrite entry."""
    return STATE_DIR / f"rewrite-entry-{position}.json"


def write_rewrite_entries(plan: dict[str, Any]) -> None:
    """Write one independently verifiable record for each rebase position."""
    for position, (source, audit_entry) in enumerate(
        zip(
            plan["scan"]["commits"],
            plan["audit"]["commits"],
            strict=True,
        ),
        start=1,
    ):
        reword = audit_entry["verdict"] == "REWORD"
        write_json(
            rewrite_entry_path(position),
            {
                "schema": 1,
                "base": plan["base"],
                "source_head": plan["source_head"],
                "position": position,
                "source_sha": source["sha"],
                "tree": source["tree"],
                "author": source["author"],
                "signed": source["signed"],
                "reword": reword,
                "source_message": source["message"],
                "expected_message": (
                    audit_entry["proposed_message"].rstrip("\n")
                    if reword
                    else source["message"]
                ),
            },
        )


def load_rewrite_entry(position: int) -> dict[str, Any]:
    """Load one frozen position record without rereading the complete plan."""
    entry = load_json(rewrite_entry_path(position))
    required_strings = (
        "base",
        "source_head",
        "source_sha",
        "tree",
        "author",
        "expected_message",
    )
    if (
        entry.get("schema") != 1
        or entry.get("position") != position
        or not all(nonempty_string(entry.get(field)) for field in required_strings)
        or not isinstance(entry.get("source_message"), str)
        or not isinstance(entry.get("signed"), bool)
        or not isinstance(entry.get("reword"), bool)
    ):
        raise SystemExit(f"invalid rewrite entry for position {position}")
    return entry


def finalized_audit(
    plan: dict[str, Any],
    scan: dict[str, Any],
) -> dict[str, Any]:
    """Translate a successfully applied rewrite plan into an all-KEEP audit."""
    source_audit = plan["audit"]
    entries: list[dict[str, Any]] = []
    for source_entry, current in zip(
        source_audit["commits"],
        scan["commits"],
        strict=True,
    ):
        reworded = source_entry["verdict"] == "REWORD"
        entry: dict[str, Any] = {
            "sha": current["sha"],
            "subject": current["subject"],
            "signals": current["signals"],
            "verdict": "KEEP",
            "reason": (
                "The replacement now matches the audited patch and series position."
                if reworded
                else source_entry["reason"]
            ),
            "patch_fidelity": source_entry["patch_fidelity"],
        }
        if "series_transition" in source_entry:
            entry["series_transition"] = source_entry["series_transition"]
        false_positives = source_entry.get(
            "proposed_signal_false_positives"
            if reworded
            else "signal_false_positives"
        )
        if false_positives:
            entry["signal_false_positives"] = false_positives
        entries.append(entry)
    return {
        "schema": 1,
        "mode": "refine",
        "base": scan["base"],
        "head": scan["head"],
        "conventions": source_audit["conventions"],
        "commits": entries,
    }


def cmd_state_dir(_: argparse.Namespace) -> None:
    """Print the state directory."""
    print(STATE_DIR)


def cmd_check_range(args: argparse.Namespace) -> None:
    """Validate and print the canonical explicit base."""
    require_stable_head()
    base, _ = validate_range(args.base, reject_shared=not args.audit_only)
    print(base)


def cmd_inspect(args: argparse.Namespace) -> None:
    """Print a read-only current-range scan."""
    require_stable_head()
    base, _ = validate_range(args.base, reject_shared=False)
    print(json.dumps(scan_document(base, mode="audit-only"), indent=2))


def cmd_start(args: argparse.Namespace) -> None:
    """Start a default mutating refinement."""
    base, commits = validate_range(args.base, reject_shared=True)
    require_safe_state_dir()
    reject_non_rebase_operations()
    rebase_active, _ = active_rebase()
    if rebase_active:
        raise SystemExit("refusing to start during a rebase")
    require_clean_worktree()
    branch_ref = current_branch_ref()
    if branch_ref is None:
        raise SystemExit("refine-commit-messages requires a named local branch")
    original_head = git_output("rev-parse", "HEAD")
    recovery_ref = create_recovery_ref(original_head)
    original = original_document(base, recovery_ref)
    clear_state_dir()
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    write_json(PRE_COMMITS_PATH, original)
    checkpoint: dict[str, Any] = {
        "schema": 1,
        "created_at": now(),
        "phase": "started",
        "base": base,
        "branch_ref": branch_ref,
        "original_head": original_head,
        "original_count": len(commits),
        "recovery_ref": recovery_ref,
        "protected_refs": protected_local_refs(commits, branch_ref),
        "events": [{"at": now(), "event": "start", "base": base}],
    }
    save_checkpoint(checkpoint)
    print(CHECKPOINT_PATH)


def cmd_status(args: argparse.Namespace) -> None:
    """Print checkpoint status."""
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
    print(f"recovery_ref: {state.get('recovery_ref', 'unknown')}")


def cmd_check_resume(_: argparse.Namespace) -> None:
    """Validate resumability and print the canonical base."""
    data, base, _, _ = validate_checkpoint(require_current=True)
    if data.get("phase") == "complete":
        raise SystemExit(
            "refine-commit-messages is already complete; start a fresh run "
            "to audit the range again"
        )
    print(base)


def cmd_recovery_ref(_: argparse.Namespace) -> None:
    """Print the checkpoint recovery ref."""
    data = load_checkpoint()
    recovery_ref = data.get("recovery_ref")
    if not nonempty_string(recovery_ref):
        raise SystemExit("no recovery ref in refine-commit-messages checkpoint")
    print(recovery_ref)


def cmd_mark(args: argparse.Namespace) -> None:
    """Record refinement progress."""
    data = load_checkpoint()
    if data.get("schema") != 1:
        raise SystemExit("no refine-commit-messages checkpoint; run start first")
    if data.get("phase") == "complete":
        raise SystemExit(
            "refine-commit-messages is already complete; start a fresh run "
            "before recording more work"
        )
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
    print(CHECKPOINT_PATH)


def cmd_scan(args: argparse.Namespace) -> None:
    """Write a checkpoint-bound scan of the current series."""
    _, checkpoint_base, rebase_active, _ = validate_checkpoint(require_current=True)
    base, _ = validate_range(args.base, reject_shared=False)
    if base != checkpoint_base:
        raise SystemExit("scan base does not match the checkpoint")
    if rebase_active:
        raise SystemExit("cannot scan during a rebase")
    require_clean_worktree()
    write_json(SCAN_PATH, scan_document(base, mode="refine"))
    print(SCAN_PATH)


def cmd_validate_audit(args: argparse.Namespace) -> None:
    """Validate a working or final structured audit."""
    base, _ = validate_range(args.base, reject_shared=False)
    if args.audit_only:
        require_stable_head()
        if args.audit_file is None:
            raise SystemExit("--audit-only requires --audit-file")
        audit = load_external_json(args.audit_file)
        scan = scan_document(base, mode="audit-only")
        validate_audit_document(
            audit,
            scan,
            allow_reword=True,
            expected_mode="audit-only",
        )
        print(f"audit valid for {scan['commit_count']} commits")
        return
    _, checkpoint_base, rebase_active, _ = validate_checkpoint(require_current=True)
    if base != checkpoint_base:
        raise SystemExit("audit base does not match the checkpoint")
    if rebase_active:
        raise SystemExit("cannot validate the audit during a rebase")
    scan = scan_document(base, mode="refine")
    saved_scan = load_json(SCAN_PATH)
    if saved_scan != scan:
        raise SystemExit("scan.json is stale; run scan again")
    validate_audit_document(
        load_json(AUDIT_PATH),
        scan,
        allow_reword=args.allow_reword,
        expected_mode="refine",
    )
    print(f"audit valid for {scan['commit_count']} commits")


def checked_todo_path(supplied_path: Path) -> Path:
    """Resolve a rebase todo only inside the current worktree's Git directory."""
    if supplied_path.is_symlink():
        raise SystemExit(f"refusing to edit a symlinked rebase todo: {supplied_path}")
    try:
        todo_path = supplied_path.resolve(strict=True)
        allowed_root = git_dir().resolve(strict=True)
    except OSError as error:
        raise SystemExit(
            f"cannot resolve rebase todo {supplied_path}: {error}"
        ) from error
    expected_paths = {
        allowed_root / directory / "git-rebase-todo"
        for directory in ("rebase-merge", "rebase-apply")
    }
    if (
        not todo_path.is_file()
        or not todo_path.is_relative_to(allowed_root)
        or todo_path not in expected_paths
    ):
        raise SystemExit(f"refusing to edit an unsafe rebase todo: {todo_path}")
    return todo_path


def replace_todo(todo_path: Path, lines: list[str]) -> None:
    """Atomically replace one validated rebase todo."""
    mode = todo_path.stat().st_mode & 0o777
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=todo_path.parent,
            prefix=f".{todo_path.name}.",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.writelines(lines)
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, todo_path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def cmd_edit_first(args: argparse.Namespace) -> None:
    """Turn the first actionable rebase todo command from pick into edit."""
    todo_path = checked_todo_path(args.todo_file)
    lines = todo_path.read_text(encoding="utf-8").splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.lstrip()
        if not stripped.strip() or stripped.startswith("#"):
            continue
        if not stripped.startswith("pick "):
            command = stripped.split(maxsplit=1)[0]
            raise SystemExit(
                f"expected the first rebase command to be 'pick', found {command!r}"
            )
        indentation = line[: len(line) - len(stripped)]
        lines[index] = indentation + "edit " + stripped.removeprefix("pick ")
        break
    else:
        raise SystemExit("rebase todo has no actionable command")
    replace_todo(todo_path, lines)


def cmd_prepare_todo(args: argparse.Namespace) -> None:
    """Attach one invariant-normalizing callback to every rebase position."""
    data, checkpoint_base, rebase_active, _ = validate_checkpoint(
        require_current=False
    )
    if data.get("phase") != "applying" or not rebase_active:
        raise SystemExit("rewrite-plan sequence editor requires the applying phase")
    require_rewrite_helper(data)
    plan = load_rewrite_plan()
    validate_rewrite_plan_binding(plan, data, checkpoint_base)
    todo_path = checked_todo_path(args.todo_file)
    source_commits = plan["scan"]["commits"]
    output: list[str] = []
    position = 0
    for line in todo_path.read_text(encoding="utf-8").splitlines(keepends=True):
        stripped = line.lstrip()
        if not stripped.strip() or stripped.startswith("#"):
            output.append(line)
            continue
        parts = stripped.split(maxsplit=2)
        command = parts[0]
        if command != "pick" or len(parts) < 2:
            raise SystemExit(
                "rewrite-plan rebase expected only pick commands, "
                f"found {command!r}"
            )
        position += 1
        if position > len(source_commits):
            raise SystemExit("rewrite-plan rebase contains unexpected commits")
        commit = canonical_base(parts[1])
        expected = source_commits[position - 1]["sha"]
        if commit != expected:
            raise SystemExit(
                f"rewrite-plan position {position} is {commit}, expected {expected}"
            )
        output.append(line)
        command_line = shlex.join(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "apply-message",
                "--position",
                str(position),
            ]
        )
        output.append(f"exec {command_line}\n")
    if position != len(source_commits):
        raise SystemExit(
            "rewrite-plan rebase commit count changed: "
            f"expected {len(source_commits)}, found {position}"
        )
    replace_todo(todo_path, output)


def cmd_begin_reword(args: argparse.Namespace) -> None:
    """Start a controlled rebase that stops at one exact series position."""
    data, checkpoint_base, rebase_active, originals = validate_checkpoint(
        require_current=True
    )
    if data.get("phase") == "complete":
        raise SystemExit(
            "refine-commit-messages is already complete; start a fresh run "
            "before rewording"
        )
    base = canonical_base(args.base)
    if base != checkpoint_base:
        raise SystemExit("reword base does not match the checkpoint")
    if rebase_active:
        raise SystemExit("a checkpoint rebase is already active")
    require_clean_worktree()
    if not 1 <= args.position <= len(originals):
        raise SystemExit("reword position is outside the original range")

    current = records_for(base)
    target = canonical_base(args.target)
    selected = current[args.position - 1]
    if selected["sha"] != target:
        raise SystemExit(
            f"position {args.position} is {selected['sha']}, not requested target "
            f"{target}"
        )

    data["phase"] = "rewriting"
    data.setdefault("events", []).append(
        {
            "at": now(),
            "event": "begin-reword",
            "position": args.position,
            "target": target,
        }
    )
    save_checkpoint(data)

    environment = os.environ.copy()
    environment["GIT_SEQUENCE_EDITOR"] = shlex.join(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "edit-first",
        ]
    )
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "rebase.abbreviateCommands=false",
            "-c",
            "rebase.autoSquash=false",
            "-c",
            "rebase.updateRefs=false",
            "-c",
            "rebase.rebaseMerges=false",
            "-c",
            "rebase.autoStash=false",
            "rebase",
            "--keep-empty",
            "-i",
            f"{target}^",
        ],
        text=True,
        env=environment,
        check=False,
    )
    if result.returncode:
        raise SystemExit(
            f"controlled rebase failed to stop at position {args.position}"
        )

    _, _, active_after, _ = validate_checkpoint(require_current=False)
    if not active_after:
        raise SystemExit(
            "controlled rebase completed without the requested edit stop; "
            "inspect the recovery ref before continuing"
        )
    stopped = commit_record("HEAD", args.position, len(originals))
    errors = compare_invariants(
        [originals[args.position - 1]],
        [{**stopped, "position": args.position}],
    )
    if stopped["message"] != originals[args.position - 1]["message"]:
        errors.append(
            f"position {args.position}: rebase stopped at an unexpected message"
        )
    if errors:
        raise SystemExit(
            "controlled rebase stop verification failed:\n" + "\n".join(errors)
        )
    print(f"stopped at position {args.position} for message-only amendment")


def cmd_apply_message(args: argparse.Namespace) -> None:
    """Restore one frozen position's expected message and signature state."""
    data = load_checkpoint()
    reject_non_rebase_operations()
    rebase_active, rebase_branch = active_rebase()
    branch_ref = data.get("branch_ref")
    checkpoint_base = data.get("base")
    original_count = data.get("original_count")
    rewrite_source_head = data.get("rewrite_source_head")
    rewrite_positions = data.get("rewrite_positions")
    if (
        data.get("schema") != 1
        or data.get("phase") != "applying"
        or not rebase_active
        or not nonempty_string(branch_ref)
        or rebase_branch != branch_ref
        or not nonempty_string(checkpoint_base)
        or not isinstance(original_count, int)
        or not nonempty_string(rewrite_source_head)
        or not isinstance(rewrite_positions, list)
        or not all(isinstance(position, int) for position in rewrite_positions)
        or not 1 <= args.position <= original_count
    ):
        raise SystemExit("apply-message requires the active rewrite-plan rebase")
    require_rewrite_helper(data)
    assert isinstance(checkpoint_base, str)
    assert isinstance(rewrite_source_head, str)
    entry = load_rewrite_entry(args.position)
    if (
        entry["base"] != checkpoint_base
        or entry["source_head"] != rewrite_source_head
        or entry["reword"] != (args.position in rewrite_positions)
    ):
        raise SystemExit("rewrite entry does not match the active checkpoint attempt")
    if latest_rebase_pick() != entry["source_sha"]:
        raise SystemExit(
            f"apply-message position {args.position} does not match "
            "the active rebase command"
        )
    expected_message = entry["expected_message"]
    current = commit_record("HEAD", args.position, original_count)
    errors = compare_invariants(
        [entry],
        [{**current, "position": args.position}],
        include_signature=False,
    )
    if errors:
        raise SystemExit(
            "rewrite callback changed commit content:\n" + "\n".join(errors)
        )

    if current["message"] not in {
        entry["source_message"],
        expected_message,
    }:
        raise SystemExit(
            f"position {args.position} matches neither its source nor "
            "its expected message"
        )

    amended = (
        current["message"] != expected_message
        or current["signed"] != entry["signed"]
    )
    if amended:
        signing_option = "--gpg-sign" if entry["signed"] else "--no-gpg-sign"
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "commit",
                "--amend",
                "--allow-empty",
                signing_option,
                "-F",
                "-",
            ],
            text=True,
            input=expected_message + "\n",
            check=False,
        )
        if result.returncode:
            raise SystemExit(
                f"message or signature amendment failed at position "
                f"{args.position}; inspect the Git and hook output before resuming"
            )
        amended = True

    revised = commit_record("HEAD", args.position, original_count)
    errors = compare_invariants(
        [entry],
        [{**revised, "position": args.position}],
    )
    if revised["message"] != expected_message:
        errors.append(f"position {args.position}: expected message was not applied")
    if errors:
        raise SystemExit(
            "message/signature amendment verification failed:\n"
            + "\n".join(errors)
        )
    action = "verified" if not amended else "normalized"
    print(f"{action} message and signature at position {args.position}")


def finalize_rewrite_plan(base: str) -> None:
    """Verify one completed rebase and refresh the audit without rereading patches."""
    data, checkpoint_base, rebase_active, originals = validate_checkpoint(
        require_current=True
    )
    if base != checkpoint_base:
        raise SystemExit("rewrite plan base does not match the checkpoint")
    if rebase_active:
        raise SystemExit("cannot finalize the rewrite plan during a rebase")
    if data.get("phase") not in {"applying", "rewritten"}:
        raise SystemExit("no applied rewrite plan is ready to finalize")
    require_clean_worktree()
    plan = load_rewrite_plan()
    validate_rewrite_plan_binding(plan, data, checkpoint_base)

    scan = scan_document(base, mode="refine")
    errors = compare_invariants(originals, scan["commits"])
    if len(scan["commits"]) == len(plan["scan"]["commits"]):
        for position, (source, audit_entry, current) in enumerate(
            zip(
                plan["scan"]["commits"],
                plan["audit"]["commits"],
                scan["commits"],
                strict=True,
            ),
            start=1,
        ):
            expected_message = (
                audit_entry["proposed_message"].rstrip("\n")
                if audit_entry["verdict"] == "REWORD"
                else source["message"]
            )
            if current["message"] != expected_message:
                errors.append(
                    f"position {position}: current message does not match "
                    "the validated rewrite plan"
                )
    if errors:
        raise SystemExit(
            "one-pass message rewrite verification failed:\n" + "\n".join(errors)
        )

    audit = finalized_audit(plan, scan)
    validate_audit_document(
        audit,
        scan,
        allow_reword=False,
        expected_mode="refine",
    )
    write_json(SCAN_PATH, scan)
    write_json(AUDIT_PATH, audit)
    if data.get("phase") != "rewritten":
        data["phase"] = "rewritten"
        data.setdefault("events", []).append(
            {
                "at": now(),
                "event": "finalize-apply",
                "head": scan["head"],
                "reword_positions": plan["reword_positions"],
            }
        )
        save_checkpoint(data)
    print(
        "one-pass rewrite verified for "
        f"{len(scan['commits'])} commits and "
        f"{len(plan['reword_positions'])} replacement messages"
    )


def cmd_apply_audit(args: argparse.Namespace) -> None:
    """Apply every validated REWORD in one controlled interactive rebase."""
    data, checkpoint_base, rebase_active, _ = validate_checkpoint(
        require_current=True
    )
    if data.get("phase") == "complete":
        raise SystemExit(
            "refine-commit-messages is already complete; start a fresh run "
            "before applying another audit"
        )
    base = canonical_base(args.base)
    if base != checkpoint_base:
        raise SystemExit("rewrite base does not match the checkpoint")
    if rebase_active:
        raise SystemExit("a checkpoint rebase is already active")
    require_clean_worktree()
    scan = scan_document(base, mode="refine")
    if load_json(SCAN_PATH) != scan:
        raise SystemExit("scan.json is stale; run scan again")
    audit = load_json(AUDIT_PATH)
    validate_audit_document(
        audit,
        scan,
        allow_reword=True,
        expected_mode="refine",
    )
    plan = build_rewrite_plan(audit, scan)
    if not plan["reword_positions"]:
        raise SystemExit("audit has no REWORD entries; proceed to completion")
    write_json(REWRITE_PLAN_PATH, plan)
    write_rewrite_entries(plan)
    rewrite_helper = snapshot_rewrite_helper()

    data["phase"] = "applying"
    data["rewrite_helper"] = str(rewrite_helper)
    data["rewrite_source_head"] = plan["source_head"]
    data["rewrite_positions"] = plan["reword_positions"]
    data.setdefault("events", []).append(
        {
            "at": now(),
            "event": "apply-audit",
            "source_head": scan["head"],
            "reword_positions": plan["reword_positions"],
        }
    )
    save_checkpoint(data)

    environment = os.environ.copy()
    environment["GIT_SEQUENCE_EDITOR"] = shlex.join(
        [
            sys.executable,
            str(rewrite_helper),
            "prepare-todo",
        ]
    )
    result = subprocess.run(
        [
            "git",
            "--no-optional-locks",
            "-c",
            "rebase.abbreviateCommands=false",
            "-c",
            "rebase.autoSquash=false",
            "-c",
            "rebase.updateRefs=false",
            "-c",
            "rebase.rebaseMerges=false",
            "-c",
            "rebase.autoStash=false",
            "-c",
            "rebase.rescheduleFailedExec=true",
            "-c",
            "commit.gpgSign=false",
            "rebase",
            "--keep-empty",
            "-i",
            base,
        ],
        text=True,
        env=environment,
        check=False,
    )
    if result.returncode:
        active_after, _ = active_rebase()
        if not active_after and git_output("rev-parse", "HEAD") == scan["head"]:
            raise SystemExit(
                "one-pass message rewrite did not start; inspect the rebase "
                "error, then rerun apply-audit from the existing checkpoint"
            )
        if not active_after:
            raise SystemExit(
                "one-pass message rewrite ended after HEAD changed; run "
                "finalize-apply to verify the result or use the recovery ref"
            )
        raise SystemExit(
            "one-pass message rewrite stopped; inspect the active rebase, "
            "then continue it and run finalize-apply"
        )
    finalize_rewrite_plan(base)


def cmd_finalize_apply(args: argparse.Namespace) -> None:
    """Finalize an applied rewrite plan after an interrupted caller resumes it."""
    finalize_rewrite_plan(canonical_base(args.base))


def load_external_json(path: Path) -> dict[str, Any]:
    """Load a caller-selected audit-only JSON file."""
    if path.is_symlink() or not path.is_file():
        raise SystemExit(f"missing or unsafe audit file: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SystemExit(f"invalid JSON in {path}: {error}") from error
    if not isinstance(value, dict):
        raise SystemExit(f"expected a JSON object in {path}")
    return value


def cmd_verify_head(args: argparse.Namespace) -> None:
    """Verify the edited commit at an active rebase stop."""
    _, _, rebase_active, originals = validate_checkpoint(require_current=False)
    if not rebase_active:
        raise SystemExit("verify-head requires an active rebase")
    if not 1 <= args.position <= len(originals):
        raise SystemExit("verify-head position is outside the original range")
    current = commit_record("HEAD", args.position, len(originals))
    errors = compare_invariants(
        [originals[args.position - 1]],
        [{**current, "position": args.position}],
    )
    if errors:
        raise SystemExit("message-only stop verification failed:\n" + "\n".join(errors))
    print(f"position {args.position} preserves tree and author metadata")


def cmd_verify(args: argparse.Namespace) -> None:
    """Verify the complete current range against the original sequence."""
    _, checkpoint_base, rebase_active, originals = validate_checkpoint(
        require_current=True
    )
    base, _ = validate_range(args.base, reject_shared=False)
    if base != checkpoint_base:
        raise SystemExit("verification base does not match the checkpoint")
    if rebase_active:
        raise SystemExit("cannot verify the final range during a rebase")
    require_clean_worktree()
    current = records_for(base)
    errors = compare_invariants(originals, current)
    if errors:
        raise SystemExit("message-only verification failed:\n" + "\n".join(errors))
    print(f"message-only invariants hold for {len(current)} commits")


def cmd_complete(args: argparse.Namespace) -> None:
    """Run the fail-closed completion gate."""
    data, checkpoint_base, rebase_active, originals = validate_checkpoint(
        require_current=True
    )
    if data.get("phase") == "complete":
        raise SystemExit(
            "refine-commit-messages is already complete; completion is not repeatable"
        )
    base, _ = validate_range(args.base, reject_shared=True)
    if base != checkpoint_base:
        raise SystemExit("completion base does not match the checkpoint")
    if rebase_active:
        raise SystemExit("cannot complete during a rebase")
    require_clean_worktree()
    scan = scan_document(base, mode="refine")
    if load_json(SCAN_PATH) != scan:
        raise SystemExit("scan.json is stale; run scan again")
    validate_audit_document(
        load_json(AUDIT_PATH),
        scan,
        allow_reword=False,
        expected_mode="refine",
    )
    current = scan["commits"]
    errors = compare_invariants(originals, current)
    if errors:
        raise SystemExit("message-only completion failed:\n" + "\n".join(errors))
    post = {
        "schema": 1,
        "base": base,
        "head": git_output("rev-parse", "HEAD"),
        "commits": [immutable_record(record) for record in current],
    }
    write_json(STATE_DIR / "post-commits.json", post)
    data["phase"] = "complete"
    data.setdefault("events", []).append(
        {
            "at": now(),
            "event": "complete",
            "head": post["head"],
            "commit_count": len(current),
        }
    )
    save_checkpoint(data)
    print(CHECKPOINT_PATH)


def build_parser() -> argparse.ArgumentParser:
    """Build the helper CLI."""
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)

    state_dir = commands.add_parser("state-dir")
    state_dir.set_defaults(func=cmd_state_dir)

    check_range = commands.add_parser("check-range")
    check_range.add_argument("--base", required=True)
    check_range.add_argument("--audit-only", action="store_true")
    check_range.set_defaults(func=cmd_check_range)

    inspect = commands.add_parser("inspect")
    inspect.add_argument("--base", required=True)
    inspect.set_defaults(func=cmd_inspect)

    start = commands.add_parser("start")
    start.add_argument("--base", required=True)
    start.set_defaults(func=cmd_start)

    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=cmd_status)

    check_resume = commands.add_parser("check-resume")
    check_resume.set_defaults(func=cmd_check_resume)

    recovery_ref = commands.add_parser("recovery-ref")
    recovery_ref.set_defaults(func=cmd_recovery_ref)

    mark = commands.add_parser("mark")
    mark.add_argument("--phase")
    mark.add_argument("--note")
    mark.set_defaults(func=cmd_mark)

    scan = commands.add_parser("scan")
    scan.add_argument("--base", required=True)
    scan.set_defaults(func=cmd_scan)

    validate_audit_parser = commands.add_parser("validate-audit")
    validate_audit_parser.add_argument("--base", required=True)
    validate_audit_parser.add_argument("--allow-reword", action="store_true")
    validate_audit_parser.add_argument("--audit-only", action="store_true")
    validate_audit_parser.add_argument("--audit-file", type=Path)
    validate_audit_parser.set_defaults(func=cmd_validate_audit)

    edit_first = commands.add_parser("edit-first")
    edit_first.add_argument("todo_file", type=Path)
    edit_first.set_defaults(func=cmd_edit_first)

    prepare_todo = commands.add_parser("prepare-todo")
    prepare_todo.add_argument("todo_file", type=Path)
    prepare_todo.set_defaults(func=cmd_prepare_todo)

    begin_reword = commands.add_parser("begin-reword")
    begin_reword.add_argument("--base", required=True)
    begin_reword.add_argument("--position", required=True, type=int)
    begin_reword.add_argument("--target", required=True)
    begin_reword.set_defaults(func=cmd_begin_reword)

    apply_message = commands.add_parser("apply-message")
    apply_message.add_argument("--position", required=True, type=int)
    apply_message.set_defaults(func=cmd_apply_message)

    apply_audit = commands.add_parser("apply-audit")
    apply_audit.add_argument("--base", required=True)
    apply_audit.set_defaults(func=cmd_apply_audit)

    finalize_apply = commands.add_parser("finalize-apply")
    finalize_apply.add_argument("--base", required=True)
    finalize_apply.set_defaults(func=cmd_finalize_apply)

    verify_head = commands.add_parser("verify-head")
    verify_head.add_argument("--position", required=True, type=int)
    verify_head.set_defaults(func=cmd_verify_head)

    verify = commands.add_parser("verify")
    verify.add_argument("--base", required=True)
    verify.set_defaults(func=cmd_verify)

    complete = commands.add_parser("complete")
    complete.add_argument("--base", required=True)
    complete.set_defaults(func=cmd_complete)
    return parser


def main() -> int:
    """Run the selected helper command."""
    args = build_parser().parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
