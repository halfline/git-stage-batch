#!/usr/bin/env python3
"""Checkpoint publication of an unpublished commit range."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus, urlsplit


SCHEMA = 1
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z(?:-[1-9][0-9]*)?$")
IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")
PROJECT_PATH = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+$")
HOST_URL = re.compile(
    r"^https?://[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
    r"(?::[1-9][0-9]{0,4})?$"
)
OBJECT_ID = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
QUICK_ACTION = re.compile(r"(?im)^[ \t]*/[a-z][a-z0-9_-]*(?:[ \t]|$)")
GITLAB_DRAFT_TITLE = re.compile(
    r"(?i)^(?:(?:draft|wip)(?:\s*:|\s+)|\[(?:draft|wip)\]|\((?:draft|wip)\))"
)
PHASES = ("started", "planned", "validated", "publishing", "published")
PUBLICATION_STATUSES = ("ready", "draft")
PROVIDERS = ("github", "gitlab")
TRANSPORTS = ("github-stack", "gitlab-stack", "ordinary")
PRECEDING_REVIEW_URL = "{{PRECEDING_REVIEW_URL}}"


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
    """Return stripped Git output."""
    return git_run(*args).stdout.strip()


def git_dir() -> Path:
    """Return this worktree's absolute Git directory."""
    try:
        return Path(git_output("rev-parse", "--absolute-git-dir"))
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            "publish-unpushed-commits requires a Git repository"
        ) from error


STATE_ROOT = git_dir() / "git-stage-batch" / "publish-unpushed-commits"
RUNS_DIR = STATE_ROOT / "runs"
ACTIVE_PATH = STATE_ROOT / "active.json"


def now() -> str:
    """Return an ISO-formatted UTC timestamp."""
    return dt.datetime.now(dt.timezone.utc).isoformat()


def reject_unsafe_path(path: Path) -> None:
    """Reject an existing symlink anywhere below the worktree Git directory."""
    current = path
    stop = git_dir().parent
    while current != stop:
        if current.is_symlink():
            raise SystemExit(
                f"refusing to use symlinked publication state path: {current}"
            )
        if current == current.parent:
            break
        current = current.parent


def atomic_write(path: Path, content: str) -> None:
    """Atomically replace one private state file."""
    reject_unsafe_path(path)
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
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            Path(temporary_name).unlink(missing_ok=True)


def write_json(path: Path, value: dict[str, Any]) -> None:
    """Atomically write one JSON object."""
    atomic_write(path, json.dumps(value, indent=2, sort_keys=True) + "\n")


def load_json(path: Path, *, required: bool = True) -> dict[str, Any]:
    """Strictly load one JSON object."""
    reject_unsafe_path(path)
    if path.exists() and not path.is_file():
        raise SystemExit(f"publication state path is not a regular file: {path}")
    if not path.is_file():
        if required:
            raise SystemExit(f"required publication state file is missing: {path}")
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise SystemExit(
            f"cannot read publication state from {path}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise SystemExit(f"expected a JSON object in publication state file: {path}")
    return value


def require_string(
    value: object,
    field: str,
    *,
    pattern: re.Pattern[str] | None = None,
) -> str:
    """Return one required nonempty string."""
    if not isinstance(value, str) or not value.strip():
        raise SystemExit(f"publication state field {field!r} must be a nonempty string")
    if pattern is not None and pattern.fullmatch(value) is None:
        raise SystemExit(f"publication state field {field!r} has an invalid value")
    return value


def require_integer(value: object, field: str) -> int:
    """Return one required positive integer."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SystemExit(
            f"publication state field {field!r} must be a positive integer"
        )
    return value


def require_git_branch(value: object, field: str) -> str:
    """Return one nonempty syntactically valid Git branch name."""
    branch = require_string(value, field)
    result = git_run("check-ref-format", "--branch", branch, check=False)
    if result.returncode or result.stdout.strip() != branch:
        raise SystemExit(f"publication state field {field!r} is not a Git branch")
    return branch


def require_git_remote_name(value: object, field: str) -> str:
    """Return one name suitable for a Git remote-tracking ref namespace."""
    remote = require_string(value, field)
    result = git_run(
        "check-ref-format", f"refs/remotes/{remote}/publication", check=False
    )
    if result.returncode:
        raise SystemExit(f"publication state field {field!r} is not a Git remote name")
    return remote


def canonical_review_url(
    provider: str,
    host_url: str,
    target_repository: str,
    number: int,
) -> str:
    """Return one provider review URL from validated checkpoint identity."""
    if provider == "github":
        review_path = f"{target_repository}/pull/{number}"
    else:
        review_path = f"{target_repository}/-/merge_requests/{number}"
    return f"{host_url}/{review_path}"


def require_string_list(value: object, field: str) -> list[str]:
    """Return one required list of nonempty strings."""
    if not isinstance(value, list) or not value:
        raise SystemExit(f"publication state field {field!r} must be a nonempty array")
    return [
        require_string(item, f"{field}[{position}]")
        for position, item in enumerate(value)
    ]


def file_digest(path: Path) -> str:
    """Return the SHA-256 digest of one required nonempty regular file."""
    reject_unsafe_path(path)
    if not path.is_file() or path.is_symlink():
        raise SystemExit(f"required publication body file is missing: {path}")
    try:
        content = path.read_bytes()
    except OSError as error:
        raise SystemExit(
            f"cannot read publication body file {path}: {error}"
        ) from error
    if not content.strip():
        raise SystemExit(f"publication body file is empty or blank: {path}")
    return hashlib.sha256(content).hexdigest()


def read_body(path: Path) -> str:
    """Return one required nonblank UTF-8 review-request body."""
    file_digest(path)
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise SystemExit(
            f"cannot read publication body file {path}: {error}"
        ) from error


def reject_gitlab_quick_actions(body: str, layer_id: str) -> None:
    """Reject prose that GitLab could reinterpret as collaboration mutations."""
    match = QUICK_ACTION.search(body)
    if match is not None:
        action = match.group(0).strip().split(maxsplit=1)[0]
        raise SystemExit(
            f"GitLab body for layer {layer_id} contains possible quick action "
            f"{action!r}; remove it or obtain explicit authority"
        )


def canonical_commit(revision: str) -> str:
    """Resolve one revision to a full commit identifier."""
    try:
        return git_output("rev-parse", "--verify", f"{revision}^{{commit}}")
    except subprocess.CalledProcessError as error:
        raise SystemExit(f"invalid commit revision: {revision!r}") from error


def range_commits(base: str, tip: str) -> list[str]:
    """Return a nonempty linear range in oldest-first order."""
    if git_run("merge-base", "--is-ancestor", base, tip, check=False).returncode:
        raise SystemExit(f"base {base} is not an ancestor of {tip}")
    commits = git_output("rev-list", "--reverse", f"{base}..{tip}").splitlines()
    if not commits:
        raise SystemExit("publish-unpushed-commits range is empty")
    merges = git_output("rev-list", "--merges", f"{base}..{tip}").splitlines()
    if merges:
        raise SystemExit(
            "publish-unpushed-commits supports only linear ranges; merge commits found: "
            + ", ".join(merges)
        )
    return commits


def reject_remote_containment(commits: list[str], remote: str) -> None:
    """Reject a range contained by the selected publication remote."""
    refs = git_output(
        "for-each-ref",
        "--format=%(refname)",
        "--contains",
        commits[0],
        f"refs/remotes/{remote}",
    ).splitlines()
    if refs:
        raise SystemExit(
            "refusing to publish a range already contained in the selected "
            "remote's tracking refs: " + ", ".join(refs)
        )


def require_clean_repository() -> None:
    """Reject pending work and in-progress Git operations."""
    status = git_run("status", "--porcelain=v1", "--untracked-files=normal").stdout
    if status:
        raise SystemExit("publish-unpushed-commits requires a clean working tree")
    repository_git_dir = git_dir()
    for marker in (
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "rebase-merge",
        "rebase-apply",
    ):
        if (repository_git_dir / marker).exists():
            raise SystemExit(f"refusing to publish during Git operation: {marker}")


def configured_push_url(remote: str) -> str:
    """Return the only configured push URL for one named remote."""
    result = git_run("remote", "get-url", "--push", "--all", remote, check=False)
    urls = [line for line in result.stdout.splitlines() if line]
    if result.returncode or len(urls) != 1:
        raise SystemExit(
            f"publication push remote {remote!r} must have exactly one push URL"
        )
    return urls[0]


def canonical_push_destination(url: str) -> str:
    """Remove credentials and non-routing URL data from one push destination."""
    if "://" in url:
        try:
            parsed = urlsplit(url)
            hostname = parsed.hostname
            port = parsed.port
        except ValueError as error:
            raise SystemExit("publication push remote has an invalid URL") from error
        if hostname is None:
            raise SystemExit("publication push remote URL has no hostname")
        scheme = parsed.scheme.lower()
        if scheme in ("http", "https"):
            username = ""
            query_parts = []
            for component in parsed.query.split("&"):
                key = unquote_plus(component.split("=", maxsplit=1)[0]).lower()
                if key not in ("access_token", "oauth_token", "private_token"):
                    query_parts.append(component)
            query = "&".join(query_parts)
        elif scheme == "ssh":
            username = parsed.username or ""
            query = parsed.query
        else:
            raise SystemExit("publication push remote uses an unsupported URL scheme")
        return json.dumps(
            [scheme, username, hostname.lower(), port, parsed.path, query],
            separators=(",", ":"),
        )

    scp_like = re.fullmatch(
        r"(?:(?P<user>[^/@:\s]+)@)?(?P<host>[^/:\s]+):(?P<path>.+)",
        url,
    )
    if scp_like is not None:
        return json.dumps(
            [
                "scp",
                scp_like.group("user") or "",
                scp_like.group("host").lower(),
                None,
                scp_like.group("path"),
                "",
            ],
            separators=(",", ":"),
        )
    raise SystemExit("publication push remote uses an unverifiable URL form")


def push_url_digest(remote: str) -> str:
    """Return a credential-safe identity for a named remote's push URL."""
    destination = canonical_push_destination(configured_push_url(remote))
    return hashlib.sha256(destination.encode("utf-8")).hexdigest()


def validate_push_remote(checkpoint: dict[str, Any]) -> None:
    """Reject a push remote whose configured destination changed during a run."""
    if push_url_digest(checkpoint["remote"]) != checkpoint["push_url_sha256"]:
        raise SystemExit("publication push remote URL changed after checkpoint start")


def active_run_id(*, required: bool = True) -> str | None:
    """Return the validated active run identifier."""
    active = load_json(ACTIVE_PATH, required=required)
    if not active:
        return None
    if active.get("schema") != SCHEMA:
        raise SystemExit("unsupported active publication state schema")
    return require_string(active.get("run_id"), "run_id", pattern=RUN_ID)


def run_dir(run_id: str) -> Path:
    """Return a validated run directory."""
    require_string(run_id, "run_id", pattern=RUN_ID)
    return RUNS_DIR / run_id


def checkpoint_path(run_id: str) -> Path:
    """Return one run's checkpoint path."""
    return run_dir(run_id) / "checkpoint.json"


def plan_path(run_id: str) -> Path:
    """Return one run's plan path."""
    return run_dir(run_id) / "plan.json"


def require_mapping(value: object, field: str) -> dict[str, Any]:
    """Return one required JSON object."""
    if not isinstance(value, dict):
        raise SystemExit(f"publication state field {field!r} must be an object")
    return value


def validate_body_reference(
    run_id: str,
    body_file: object,
    body_sha256: object,
    field: str,
) -> tuple[str, str]:
    """Validate one run-relative immutable body reference."""
    relative = require_string(body_file, f"{field}.file")
    parts = Path(relative).parts
    if (
        Path(relative).is_absolute()
        or ".." in parts
        or len(parts) < 2
        or parts[0] != "bodies"
    ):
        raise SystemExit(f"unsafe publication body path in {field}")
    digest = require_string(
        body_sha256,
        f"{field}.sha256",
        pattern=OBJECT_ID,
    )
    if file_digest(run_dir(run_id) / relative) != digest:
        raise SystemExit(f"publication body digest changed in {field}")
    return relative, digest


def validate_checkpoint(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and return one checkpoint document."""
    if value.get("schema") != SCHEMA:
        raise SystemExit("unsupported publication checkpoint schema")
    run_id = require_string(value.get("run_id"), "run_id", pattern=RUN_ID)
    require_string(value.get("base"), "base", pattern=OBJECT_ID)
    require_string(value.get("target_base"), "target_base", pattern=OBJECT_ID)
    source_head = require_string(
        value.get("source_head"), "source_head", pattern=OBJECT_ID
    )
    require_string(value.get("source_tree"), "source_tree", pattern=OBJECT_ID)
    require_git_branch(value.get("original_branch"), "original_branch")
    provider = value.get("provider")
    if provider not in PROVIDERS:
        raise SystemExit("invalid publication provider")
    require_string(value.get("host_url"), "host_url", pattern=HOST_URL)
    target_repository = require_string(
        value.get("target_repository"), "target_repository", pattern=PROJECT_PATH
    )
    head_repository = require_string(
        value.get("head_repository"), "head_repository", pattern=PROJECT_PATH
    )
    if provider == "github":
        if target_repository.count("/") != 1 or head_repository.count("/") != 1:
            raise SystemExit("GitHub repositories must use owner/name paths")
        if "target_project_id" in value or "head_project_id" in value:
            raise SystemExit("GitHub checkpoints cannot contain GitLab project IDs")
        target_repository_id = require_integer(
            value.get("target_repository_id"), "target_repository_id"
        )
        head_repository_id = require_integer(
            value.get("head_repository_id"), "head_repository_id"
        )
        if (target_repository == head_repository) != (
            target_repository_id == head_repository_id
        ):
            raise SystemExit("GitHub repository paths and numeric IDs disagree")
    else:
        if "target_repository_id" in value or "head_repository_id" in value:
            raise SystemExit("GitLab checkpoints cannot contain GitHub repository IDs")
        target_project_id = require_integer(
            value.get("target_project_id"), "target_project_id"
        )
        head_project_id = require_integer(
            value.get("head_project_id"), "head_project_id"
        )
        if (target_repository == head_repository) != (
            target_project_id == head_project_id
        ):
            raise SystemExit("GitLab project paths and numeric IDs disagree")
    require_git_remote_name(value.get("remote"), "remote")
    require_string(
        value.get("push_url_sha256"),
        "push_url_sha256",
        pattern=OBJECT_ID,
    )
    head_push_url_sha256s = require_string_list(
        value.get("head_push_url_sha256s"), "head_push_url_sha256s"
    )
    if len(head_push_url_sha256s) != len(set(head_push_url_sha256s)):
        raise SystemExit("checkpointed head repository push URLs must be unique")
    for position, digest in enumerate(head_push_url_sha256s):
        require_string(
            digest,
            f"head_push_url_sha256s[{position}]",
            pattern=OBJECT_ID,
        )
    if value["push_url_sha256"] not in head_push_url_sha256s:
        raise SystemExit("publication push remote is not bound to the head repository")
    require_git_branch(value.get("trunk"), "trunk")
    if value.get("requested_status") not in PUBLICATION_STATUSES:
        raise SystemExit("invalid requested publication status")
    if value.get("phase") not in PHASES:
        raise SystemExit("invalid publication phase")
    normalized_fields = ("normalized_tip", "normalized_tree")
    normalized_present = [field in value for field in normalized_fields]
    if any(normalized_present) and not all(normalized_present):
        raise SystemExit("publication checkpoint has incomplete normalized state")
    for field in normalized_fields:
        if field in value:
            require_string(value.get(field), field, pattern=OBJECT_ID)
    if value.get("phase") != "started" and not all(normalized_present):
        raise SystemExit("publication checkpoint phase requires normalized state")
    recovery_ref = require_string(value.get("recovery_ref"), "recovery_ref")
    expected_recovery_ref = (
        f"refs/git-stage-batch/publish-unpushed-commits/{run_id}/original"
    )
    if recovery_ref != expected_recovery_ref:
        raise SystemExit("invalid publication recovery ref")
    commits = require_string_list(value.get("commits"), "commits")
    if len(commits) != len(set(commits)):
        raise SystemExit("publication checkpoint commits must be unique")
    for position, commit in enumerate(commits):
        require_string(commit, f"commits[{position}]", pattern=OBJECT_ID)
    if commits[-1] != source_head:
        raise SystemExit("publication checkpoint source head must end its commit range")
    require_string(value.get("created_at"), "created_at")
    require_string(value.get("updated_at"), "updated_at")
    remote_branches = require_mapping(value.get("remote_branches"), "remote_branches")
    for layer_id, remote_branch in remote_branches.items():
        require_string(layer_id, "remote_branches key", pattern=IDENTIFIER)
        record = require_mapping(
            remote_branch,
            f"remote_branches.{layer_id}",
        )
        if record.get("layer") != layer_id:
            raise SystemExit(
                f"remote branch record key does not match layer {layer_id}"
            )
        require_git_branch(record.get("branch"), f"remote_branches.{layer_id}.branch")
        require_string(
            record.get("planned_head"),
            f"remote_branches.{layer_id}.planned_head",
            pattern=OBJECT_ID,
        )
        expected_old = record.get("expected_old")
        if expected_old is not None:
            require_string(
                expected_old,
                f"remote_branches.{layer_id}.expected_old",
                pattern=OBJECT_ID,
            )
        require_string(
            record.get("prepared_at"),
            f"remote_branches.{layer_id}.prepared_at",
        )
        confirmed_head = record.get("confirmed_head")
        confirmed_at = record.get("confirmed_at")
        if (confirmed_head is None) != (confirmed_at is None):
            raise SystemExit(
                f"remote branch record for {layer_id} has incomplete confirmation"
            )
        if confirmed_head is not None:
            require_string(
                confirmed_head,
                f"remote_branches.{layer_id}.confirmed_head",
                pattern=OBJECT_ID,
            )
            require_string(
                confirmed_at,
                f"remote_branches.{layer_id}.confirmed_at",
            )
            if confirmed_head != record["planned_head"]:
                raise SystemExit(
                    f"remote branch record for {layer_id} confirms the wrong head"
                )

    created_reviews = require_mapping(value.get("created_reviews"), "created_reviews")
    created_numbers: set[int] = set()
    created_urls: set[str] = set()
    for layer_id, created_review in created_reviews.items():
        require_string(layer_id, "created_reviews key", pattern=IDENTIFIER)
        record = require_mapping(
            created_review,
            f"created_reviews.{layer_id}",
        )
        for field in ("group", "layer"):
            require_string(
                record.get(field),
                f"created_reviews.{layer_id}.{field}",
                pattern=IDENTIFIER,
            )
        for field in ("branch", "base"):
            require_git_branch(record.get(field), f"created_reviews.{layer_id}.{field}")
        if record.get("layer") != layer_id:
            raise SystemExit(
                f"created review record key does not match layer {layer_id}"
            )
        for field in ("head", "base_head"):
            require_string(
                record.get(field),
                f"created_reviews.{layer_id}.{field}",
                pattern=OBJECT_ID,
            )
        number = require_integer(
            record.get("number"), f"created_reviews.{layer_id}.number"
        )
        url = require_string(record.get("url"), f"created_reviews.{layer_id}.url")
        expected_url = canonical_review_url(
            value["provider"],
            value["host_url"],
            value["target_repository"],
            number,
        )
        if url != expected_url:
            raise SystemExit(
                f"created review URL does not match its checkpoint identity: {layer_id}"
            )
        if number in created_numbers or url in created_urls:
            raise SystemExit("created reviews must use unique review requests")
        created_numbers.add(number)
        created_urls.add(url)
        if record.get("status") != value["requested_status"]:
            raise SystemExit(
                f"created review for {layer_id} has the wrong requested status"
            )
        validate_body_reference(
            run_id,
            record.get("body_file"),
            record.get("body_sha256"),
            f"created_reviews.{layer_id}.body",
        )
        require_string(
            record.get("observed_at"),
            f"created_reviews.{layer_id}.observed_at",
        )

    publications = require_mapping(value.get("publications"), "publications")
    phase = value["phase"]
    if phase not in ("publishing", "published") and (
        remote_branches or created_reviews or publications
    ):
        raise SystemExit("remote publication state exists before publishing")
    publication_numbers: set[int] = set()
    publication_urls: set[str] = set()
    for layer_id, publication in publications.items():
        require_string(layer_id, "publications key", pattern=IDENTIFIER)
        if not isinstance(publication, dict):
            raise SystemExit(f"publication record for {layer_id} must be an object")
        for field in ("group", "layer"):
            require_string(
                publication.get(field),
                f"publications.{layer_id}.{field}",
                pattern=IDENTIFIER,
            )
        for field in ("branch", "base"):
            require_git_branch(
                publication.get(field), f"publications.{layer_id}.{field}"
            )
        if publication["layer"] != layer_id:
            raise SystemExit(f"publication record key does not match layer {layer_id}")
        require_string(
            publication.get("head"),
            f"publications.{layer_id}.head",
            pattern=OBJECT_ID,
        )
        require_string(
            publication.get("base_head"),
            f"publications.{layer_id}.base_head",
            pattern=OBJECT_ID,
        )
        number = require_integer(
            publication.get("number"), f"publications.{layer_id}.number"
        )
        url = require_string(publication.get("url"), f"publications.{layer_id}.url")
        expected_url = canonical_review_url(
            value["provider"],
            value["host_url"],
            value["target_repository"],
            number,
        )
        if url != expected_url:
            raise SystemExit(
                f"publication URL does not match its checkpoint identity: {layer_id}"
            )
        if number in publication_numbers or url in publication_urls:
            raise SystemExit("publication records must use unique review requests")
        publication_numbers.add(number)
        publication_urls.add(url)
        if publication.get("status") != value["requested_status"]:
            raise SystemExit(
                f"publication record for {layer_id} has the wrong requested status"
            )
        stack_number = publication.get("stack_number")
        if stack_number is not None:
            require_integer(stack_number, f"publications.{layer_id}.stack_number")
        require_string(
            publication.get("verified_at"),
            f"publications.{layer_id}.verified_at",
        )
    return value


def load_checkpoint() -> dict[str, Any]:
    """Load the active checkpoint."""
    run_id = active_run_id()
    assert run_id is not None
    checkpoint = validate_checkpoint(load_json(checkpoint_path(run_id)))
    if checkpoint["run_id"] != run_id:
        raise SystemExit("active publication run does not match its checkpoint")
    return checkpoint


def save_checkpoint(checkpoint: dict[str, Any]) -> None:
    """Validate and save the active checkpoint."""
    checkpoint["updated_at"] = now()
    validate_checkpoint(checkpoint)
    write_json(checkpoint_path(checkpoint["run_id"]), checkpoint)


def new_run_id() -> str:
    """Return a collision-safe timestamp run identifier."""
    stem = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    candidate = stem
    suffix = 2
    while run_dir(candidate).exists():
        candidate = f"{stem}-{suffix}"
        suffix += 1
    return candidate


def command_start(args: argparse.Namespace) -> None:
    """Start one publication run and create its recovery ref."""
    previous_id = active_run_id(required=False)
    if previous_id is not None:
        previous = validate_checkpoint(load_json(checkpoint_path(previous_id)))
        if previous["phase"] != "published":
            raise SystemExit(
                "an unfinished publication run already exists; use resume instead"
            )

    branch = git_output("branch", "--show-current")
    if not branch:
        raise SystemExit("publish-unpushed-commits requires a named local branch")
    require_git_branch(branch, "original_branch")
    require_string(args.host_url, "host_url", pattern=HOST_URL)
    require_string(
        args.target_repository,
        "target_repository",
        pattern=PROJECT_PATH,
    )
    require_string(args.head_repository, "head_repository", pattern=PROJECT_PATH)
    if args.provider == "github":
        if (
            args.target_repository.count("/") != 1
            or args.head_repository.count("/") != 1
        ):
            raise SystemExit("GitHub repositories must use owner/name paths")
        if args.target_project_id is not None or args.head_project_id is not None:
            raise SystemExit("GitHub publication does not accept GitLab project IDs")
        require_integer(args.target_repository_id, "target_repository_id")
        require_integer(args.head_repository_id, "head_repository_id")
        if (args.target_repository == args.head_repository) != (
            args.target_repository_id == args.head_repository_id
        ):
            raise SystemExit("GitHub repository paths and numeric IDs disagree")
    else:
        if args.target_repository_id is not None or args.head_repository_id is not None:
            raise SystemExit("GitLab publication does not accept GitHub repository IDs")
        require_integer(args.target_project_id, "target_project_id")
        require_integer(args.head_project_id, "head_project_id")
    require_git_remote_name(args.remote, "remote")
    require_git_branch(args.trunk, "trunk")
    if not args.head_push_url:
        raise SystemExit("record at least one API-observed head repository push URL")
    configured_destination = canonical_push_destination(
        configured_push_url(args.remote)
    )
    observed_destinations = {
        canonical_push_destination(require_string(url, "head_push_url"))
        for url in args.head_push_url
    }
    if configured_destination not in observed_destinations:
        raise SystemExit(
            "publication push remote does not match the API-observed head repository"
        )
    head_push_url_sha256s = sorted(
        hashlib.sha256(destination.encode("utf-8")).hexdigest()
        for destination in observed_destinations
    )
    require_clean_repository()
    base = canonical_commit(args.base)
    target_base = canonical_commit(args.target_base)
    if git_run(
        "merge-base", "--is-ancestor", base, target_base, check=False
    ).returncode:
        raise SystemExit("target base must descend from the publication range base")
    source_head = canonical_commit("HEAD")
    commits = range_commits(base, source_head)
    reject_remote_containment(commits, args.remote)
    run_id = new_run_id()
    recovery_ref = f"refs/git-stage-batch/publish-unpushed-commits/{run_id}/original"
    checkpoint = {
        "schema": SCHEMA,
        "run_id": run_id,
        "phase": "started",
        "requested_status": args.status,
        "base": base,
        "target_base": target_base,
        "source_head": source_head,
        "source_tree": git_output("show", "-s", "--format=%T", source_head),
        "original_branch": branch,
        "provider": args.provider,
        "host_url": args.host_url,
        "target_repository": args.target_repository,
        "head_repository": args.head_repository,
        "remote": args.remote,
        "push_url_sha256": push_url_digest(args.remote),
        "head_push_url_sha256s": head_push_url_sha256s,
        "trunk": args.trunk,
        "recovery_ref": recovery_ref,
        "commits": commits,
        "remote_branches": {},
        "created_reviews": {},
        "publications": {},
        "created_at": now(),
        "updated_at": now(),
    }
    if args.provider == "gitlab":
        checkpoint["target_project_id"] = args.target_project_id
        checkpoint["head_project_id"] = args.head_project_id
    else:
        checkpoint["target_repository_id"] = args.target_repository_id
        checkpoint["head_repository_id"] = args.head_repository_id
    validate_checkpoint(checkpoint)
    result = git_run("update-ref", recovery_ref, source_head, "", check=False)
    if result.returncode:
        raise SystemExit(
            f"cannot create publication recovery ref: {result.stderr.strip()}"
        )
    run_dir(run_id).mkdir(parents=True, exist_ok=False)
    save_checkpoint(checkpoint)
    write_json(ACTIVE_PATH, {"schema": SCHEMA, "run_id": run_id})
    print(json.dumps(checkpoint, indent=2, sort_keys=True))


def require_list(value: object, field: str) -> list[Any]:
    """Return one required publication-data list."""
    if not isinstance(value, list):
        raise SystemExit(f"publication data field {field!r} must be an array")
    return value


def command_record_normalized(args: argparse.Namespace) -> None:
    """Freeze the rebased or otherwise normalized aggregate result."""
    checkpoint = load_checkpoint()
    if checkpoint["phase"] != "started":
        raise SystemExit("the normalized publication result is already frozen")
    tip = canonical_commit(args.tip)
    range_commits(checkpoint["target_base"], tip)
    target_tree = git_output("show", "-s", "--format=%T", checkpoint["target_base"])
    tip_tree = git_output("show", "-s", "--format=%T", tip)
    if tip_tree == target_tree:
        raise SystemExit("normalized publication result has no aggregate tree change")
    checkpoint["normalized_tip"] = tip
    checkpoint["normalized_tree"] = tip_tree
    save_checkpoint(checkpoint)
    print(json.dumps(checkpoint, indent=2, sort_keys=True))


def command_refresh_normalized(args: argparse.Namespace) -> None:
    """Replace a pre-validation result after the target trunk advances."""
    checkpoint = load_checkpoint()
    if checkpoint["phase"] not in ("started", "planned"):
        raise SystemExit(
            "the normalized publication result is frozen after validation begins"
        )
    if "normalized_tip" not in checkpoint:
        raise SystemExit("record the initial normalized publication result first")
    target_base = canonical_commit(args.target_base)
    if git_run(
        "merge-base",
        "--is-ancestor",
        checkpoint["target_base"],
        target_base,
        check=False,
    ).returncode:
        raise SystemExit("the target base did not advance by fast-forward")
    tip = canonical_commit(args.tip)
    range_commits(target_base, tip)
    target_tree = git_output("show", "-s", "--format=%T", target_base)
    tip_tree = git_output("show", "-s", "--format=%T", tip)
    if tip_tree == target_tree:
        raise SystemExit("normalized publication result has no aggregate tree change")
    checkpoint["target_base"] = target_base
    checkpoint["normalized_tip"] = tip
    checkpoint["normalized_tree"] = tip_tree
    checkpoint["phase"] = "started"
    save_checkpoint(checkpoint)
    print(json.dumps(checkpoint, indent=2, sort_keys=True))


def validate_composed_plan_tree(
    checkpoint: dict[str, Any],
    integration_tip: str,
    groups: list[dict[str, Any]],
) -> None:
    """Require the independent group outcomes to compose to the frozen tree."""
    with tempfile.TemporaryDirectory(prefix="git-stage-batch-publish-index-") as temp:
        environment = {**os.environ, "GIT_INDEX_FILE": str(Path(temp) / "index")}
        read_tree = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "read-tree",
                checkpoint["target_base"],
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        if read_tree.returncode:
            raise SystemExit(
                "cannot initialize publication aggregate validation: "
                + read_tree.stderr.decode(errors="replace").strip()
            )

        for group in groups:
            group_tip = group["layers"][-1]["tip"]
            patch = subprocess.run(
                [
                    "git",
                    "--no-optional-locks",
                    "diff",
                    "--binary",
                    "--full-index",
                    "--no-ext-diff",
                    "--no-renames",
                    "--no-textconv",
                    checkpoint["target_base"],
                    group_tip,
                    "--",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if patch.returncode:
                raise SystemExit(
                    f"cannot read aggregate changes for publication group "
                    f"{group['id']}: " + patch.stderr.decode(errors="replace").strip()
                )
            apply = subprocess.run(
                [
                    "git",
                    "--no-optional-locks",
                    "apply",
                    "--cached",
                    "--3way",
                    "--whitespace=nowarn",
                    "-",
                ],
                input=patch.stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                check=False,
            )
            if apply.returncode:
                raise SystemExit(
                    f"publication group {group['id']} does not compose cleanly "
                    "with the preceding groups: "
                    + apply.stderr.decode(errors="replace").strip()
                )

        comparison = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "diff-index",
                "--cached",
                "--quiet",
                integration_tip,
                "--",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            check=False,
        )
        if comparison.returncode == 1:
            raise SystemExit(
                "publication groups do not compose the normalized aggregate tree"
            )
        if comparison.returncode:
            raise SystemExit(
                "cannot compare the publication aggregate with its normalized tree: "
                + comparison.stderr.decode(errors="replace").strip()
            )


def validate_plan(
    value: dict[str, Any],
    checkpoint: dict[str, Any],
    *,
    check_branches: bool = True,
    require_body_hash: bool = False,
) -> dict[str, Any]:
    """Validate one complete publication plan."""
    if value.get("schema") != SCHEMA:
        raise SystemExit("unsupported publication plan schema")
    if "normalized_tree" not in checkpoint:
        raise SystemExit("record the normalized publication result before its plan")
    if value.get("base") != checkpoint["target_base"]:
        raise SystemExit("publication plan base does not match the checkpoint")
    integration_tip = canonical_commit(
        require_string(value.get("integration_tip"), "integration_tip")
    )
    if integration_tip != checkpoint["normalized_tip"]:
        raise SystemExit("publication plan integration tip is not the normalized tip")
    integration_tree = git_output("show", "-s", "--format=%T", integration_tip)
    if integration_tree != checkpoint["normalized_tree"]:
        raise SystemExit(
            "publication plan does not preserve the normalized aggregate tree"
        )

    groups = require_list(value.get("groups"), "groups")
    if not groups:
        raise SystemExit("publication plan must contain at least one group")
    group_ids: set[str] = set()
    layer_ids: set[str] = set()
    branches: set[str] = set()
    normalized_groups: list[dict[str, Any]] = []
    target_tree = git_output("show", "-s", "--format=%T", checkpoint["target_base"])
    for group_position, raw_group in enumerate(groups, start=1):
        if not isinstance(raw_group, dict):
            raise SystemExit("every publication group must be an object")
        group_id = require_string(raw_group.get("id"), "group.id", pattern=IDENTIFIER)
        if group_id in group_ids:
            raise SystemExit(f"duplicate publication group identifier: {group_id}")
        group_ids.add(group_id)
        transport = raw_group.get("transport")
        if transport not in TRANSPORTS:
            raise SystemExit(f"invalid publication transport for group {group_id}")
        layers = require_list(raw_group.get("layers"), "group.layers")
        if not layers:
            raise SystemExit(f"publication group {group_id} has no layers")
        stack_provider = {
            "github-stack": "github",
            "gitlab-stack": "gitlab",
        }.get(transport)
        if stack_provider is not None and checkpoint["provider"] != stack_provider:
            raise SystemExit(
                f"publication transport {transport} does not match provider "
                f"{checkpoint['provider']}"
            )
        if stack_provider is not None and len(layers) < 2:
            raise SystemExit("a provider stack must contain at least two layers")
        if transport == "github-stack" and len(layers) > 100:
            raise SystemExit(
                "a GitHub stack cannot contain more than one hundred layers"
            )
        if transport == "gitlab-stack" and len(layers) > 10:
            raise SystemExit("a GitLab stack cannot contain more than ten layers")
        if (
            stack_provider is not None
            and checkpoint["head_repository"] != checkpoint["target_repository"]
        ):
            raise SystemExit("a provider stack cannot cross a repository fork")
        previous_tip = checkpoint["target_base"]
        previous_branch = checkpoint["trunk"]
        normalized_layers: list[dict[str, Any]] = []
        for layer_position, raw_layer in enumerate(layers, start=1):
            if not isinstance(raw_layer, dict):
                raise SystemExit("every publication layer must be an object")
            layer_id = require_string(
                raw_layer.get("id"), "layer.id", pattern=IDENTIFIER
            )
            branch = require_git_branch(raw_layer.get("branch"), "layer.branch")
            if branch == checkpoint["trunk"]:
                raise SystemExit(
                    "a publication layer cannot use the target trunk branch"
                )
            base_branch = require_git_branch(
                raw_layer.get("base_branch"), "layer.base_branch"
            )
            title = require_string(raw_layer.get("title"), "layer.title")
            if "\n" in title or "\r" in title:
                raise SystemExit(f"review-request title must be one line: {layer_id}")
            if checkpoint["provider"] == "gitlab":
                if checkpoint["requested_status"] == "draft" and not title.startswith(
                    "Draft: "
                ):
                    raise SystemExit(
                        f"GitLab draft title must begin with 'Draft: ': {layer_id}"
                    )
                if (
                    checkpoint["requested_status"] == "ready"
                    and GITLAB_DRAFT_TITLE.match(title) is not None
                ):
                    raise SystemExit(
                        f"GitLab ready title cannot use a draft prefix: {layer_id}"
                    )
            body_file = require_string(raw_layer.get("body_file"), "layer.body_file")
            tip = canonical_commit(require_string(raw_layer.get("tip"), "layer.tip"))
            if layer_id in layer_ids:
                raise SystemExit(f"duplicate publication layer identifier: {layer_id}")
            if branch in branches:
                raise SystemExit(f"duplicate publication branch: {branch}")
            layer_ids.add(layer_id)
            branches.add(branch)
            if check_branches:
                branch_tip = git_run(
                    "show-ref",
                    "--verify",
                    "--hash",
                    f"refs/heads/{branch}",
                    check=False,
                )
                if branch_tip.returncode or branch_tip.stdout.strip() != tip:
                    raise SystemExit(
                        f"publication branch does not match planned tip: {branch}"
                    )
            if git_run(
                "merge-base", "--is-ancestor", previous_tip, tip, check=False
            ).returncode:
                raise SystemExit(
                    f"publication group {group_id} is not cumulative at layer {layer_id}"
                )
            range_commits(previous_tip, tip)
            previous_tree = git_output("show", "-s", "--format=%T", previous_tip)
            tip_tree = git_output("show", "-s", "--format=%T", tip)
            if tip_tree == previous_tree:
                raise SystemExit(
                    f"publication layer {layer_id} has no incremental tree change"
                )
            body_path = run_dir(checkpoint["run_id"]) / body_file
            body_parts = Path(body_file).parts
            if (
                Path(body_file).is_absolute()
                or ".." in body_parts
                or len(body_parts) < 2
                or body_parts[0] != "bodies"
            ):
                raise SystemExit(f"unsafe body file path for layer {layer_id}")
            body = read_body(body_path)
            body_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()
            placeholder_count = body.count(PRECEDING_REVIEW_URL)
            if layer_position == 1 and placeholder_count:
                raise SystemExit(
                    f"bottom publication layer cannot name a preceding review: "
                    f"{layer_id}"
                )
            if layer_position > 1 and placeholder_count != 1:
                raise SystemExit(
                    f"dependent publication layer must contain exactly one "
                    f"{PRECEDING_REVIEW_URL} placeholder: {layer_id}"
                )
            if checkpoint["provider"] == "gitlab":
                reject_gitlab_quick_actions(body, layer_id)
            recorded_body_sha256 = raw_layer.get("body_sha256")
            if recorded_body_sha256 is None:
                if require_body_hash:
                    raise SystemExit(f"publication layer {layer_id} lacks a body hash")
            else:
                require_string(
                    recorded_body_sha256,
                    "layer.body_sha256",
                    pattern=OBJECT_ID,
                )
                if recorded_body_sha256 != body_sha256:
                    raise SystemExit(
                        f"audited review-request body changed for layer {layer_id}"
                    )
            if layer_position == 1 or transport == "ordinary":
                expected_base = checkpoint["trunk"]
            else:
                expected_base = previous_branch
            if base_branch != expected_base:
                raise SystemExit(
                    f"publication layer {layer_id} must target {expected_base}"
                )
            normalized_layers.append(
                {
                    "id": layer_id,
                    "position": layer_position,
                    "branch": branch,
                    "base_branch": base_branch,
                    "tip": tip,
                    "title": title,
                    "body_file": body_file,
                    "body_sha256": body_sha256,
                }
            )
            previous_tip = tip
            previous_branch = branch
        group_tree = git_output("show", "-s", "--format=%T", previous_tip)
        if group_tree == target_tree:
            raise SystemExit(
                f"publication group {group_id} has no aggregate tree change"
            )
        normalized_groups.append(
            {
                "id": group_id,
                "position": group_position,
                "transport": transport,
                "layers": normalized_layers,
            }
        )
    validate_composed_plan_tree(checkpoint, integration_tip, normalized_groups)
    return {
        "schema": SCHEMA,
        "base": checkpoint["target_base"],
        "integration_tip": integration_tip,
        "groups": normalized_groups,
    }


def command_record_plan(args: argparse.Namespace) -> None:
    """Validate and record the branch and review-request plan."""
    checkpoint = load_checkpoint()
    if checkpoint["phase"] not in ("started", "planned"):
        raise SystemExit("the publication plan is frozen after validation begins")
    source = Path(args.file)
    plan = validate_plan(load_json(source), checkpoint)
    write_json(plan_path(checkpoint["run_id"]), plan)
    checkpoint["phase"] = "planned"
    save_checkpoint(checkpoint)
    print(json.dumps(plan, indent=2, sort_keys=True))


def load_plan(
    checkpoint: dict[str, Any], *, check_branches: bool = True
) -> dict[str, Any]:
    """Load and revalidate the active plan."""
    plan = load_json(plan_path(checkpoint["run_id"]))
    return validate_plan(
        plan,
        checkpoint,
        check_branches=check_branches,
        require_body_hash=True,
    )


def find_layer(
    plan: dict[str, Any], layer_id: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the group and layer for one unique identifier."""
    for group in plan["groups"]:
        for layer in group["layers"]:
            if layer["id"] == layer_id:
                return group, layer
    raise SystemExit(f"unknown publication layer: {layer_id}")


def find_group(plan: dict[str, Any], group_id: str) -> dict[str, Any]:
    """Return one unique publication group."""
    for group in plan["groups"]:
        if group["id"] == group_id:
            return group
    raise SystemExit(f"unknown publication group: {group_id}")


def expected_base_head(
    checkpoint: dict[str, Any],
    plan: dict[str, Any],
    layer: dict[str, Any],
) -> str:
    """Return the immutable commit expected at one layer's direct base."""
    if layer["base_branch"] == checkpoint["trunk"]:
        return checkpoint["target_base"]
    for group in plan["groups"]:
        for candidate in group["layers"]:
            if candidate["branch"] == layer["base_branch"]:
                return candidate["tip"]
    raise SystemExit(f"cannot resolve planned base head for layer {layer['id']}")


def rendered_body(
    checkpoint: dict[str, Any],
    group: dict[str, Any],
    layer: dict[str, Any],
) -> tuple[str, str, str]:
    """Return the immutable body rendered with its preceding review URL."""
    body = read_body(run_dir(checkpoint["run_id"]) / layer["body_file"])
    if layer["position"] == 1:
        return layer["body_file"], layer["body_sha256"], body
    preceding_layer = group["layers"][layer["position"] - 2]
    preceding_review = checkpoint["created_reviews"].get(preceding_layer["id"])
    if preceding_review is None:
        raise SystemExit(
            f"create and record the preceding review before layer {layer['id']}"
        )
    rendered = body.replace(PRECEDING_REVIEW_URL, preceding_review["url"])
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    relative = f"bodies/rendered/{digest}.md"
    return relative, digest, rendered


def materialize_rendered_body(
    checkpoint: dict[str, Any],
    group: dict[str, Any],
    layer: dict[str, Any],
) -> tuple[str, str, str]:
    """Write and return one deterministic rendered review body."""
    relative, digest, body = rendered_body(checkpoint, group, layer)
    if relative != layer["body_file"]:
        atomic_write(run_dir(checkpoint["run_id"]) / relative, body)
    return relative, digest, body


def validate_progress_against_plan(
    checkpoint: dict[str, Any], plan: dict[str, Any]
) -> None:
    """Cross-check remote mutation state against the immutable plan."""
    planned_layers = {
        layer["id"]: (group, layer)
        for group in plan["groups"]
        for layer in group["layers"]
    }
    for layer_id, remote_branch in checkpoint["remote_branches"].items():
        if layer_id not in planned_layers:
            raise SystemExit(f"remote branch state names unknown layer {layer_id}")
        _group, layer = planned_layers[layer_id]
        if remote_branch["branch"] != layer["branch"]:
            raise SystemExit(f"remote branch state changed branch for layer {layer_id}")
        if remote_branch["planned_head"] != layer["tip"]:
            raise SystemExit(f"remote branch state changed head for layer {layer_id}")

    for layer_id, created_review in checkpoint["created_reviews"].items():
        if layer_id not in planned_layers:
            raise SystemExit(f"created review state names unknown layer {layer_id}")
        group, layer = planned_layers[layer_id]
        expected_fields = {
            "group": group["id"],
            "layer": layer_id,
            "branch": layer["branch"],
            "base": layer["base_branch"],
            "base_head": expected_base_head(checkpoint, plan, layer),
            "head": layer["tip"],
            "status": checkpoint["requested_status"],
        }
        if any(
            created_review.get(field) != expected
            for field, expected in expected_fields.items()
        ):
            raise SystemExit(f"created review state changed for layer {layer_id}")
        expected_body_file, expected_body_sha256, _body = rendered_body(
            checkpoint,
            group,
            layer,
        )
        if (
            created_review.get("body_file") != expected_body_file
            or created_review.get("body_sha256") != expected_body_sha256
        ):
            raise SystemExit(f"created review body changed for layer {layer_id}")
        remote_branch = checkpoint["remote_branches"].get(layer_id)
        if remote_branch is None or remote_branch.get("confirmed_head") != layer["tip"]:
            raise SystemExit(
                f"created review lacks a confirmed remote branch for {layer_id}"
            )

    for layer_id, publication in checkpoint["publications"].items():
        if layer_id not in planned_layers:
            raise SystemExit(f"publication record names unknown layer {layer_id}")
        group, layer = planned_layers[layer_id]
        if publication["group"] != group["id"]:
            raise SystemExit(f"publication record changed group for layer {layer_id}")
        if publication["branch"] != layer["branch"]:
            raise SystemExit(f"publication record changed branch for layer {layer_id}")
        if publication["base"] != layer["base_branch"]:
            raise SystemExit(f"publication record changed base for layer {layer_id}")
        if publication["head"] != layer["tip"]:
            raise SystemExit(f"publication record changed head for layer {layer_id}")
        if publication["base_head"] != expected_base_head(checkpoint, plan, layer):
            raise SystemExit(
                f"publication record changed base head for layer {layer_id}"
            )
        created_review = checkpoint["created_reviews"].get(layer_id)
        if created_review is None:
            raise SystemExit(
                f"publication record lacks created review state for {layer_id}"
            )
        for field in (
            "group",
            "layer",
            "branch",
            "base",
            "base_head",
            "head",
            "number",
            "url",
            "status",
        ):
            if publication.get(field) != created_review.get(field):
                raise SystemExit(
                    f"publication record changed created identity for {layer_id}"
                )
        remote_branch = checkpoint["remote_branches"].get(layer_id)
        if remote_branch is None or remote_branch.get("confirmed_head") != layer["tip"]:
            raise SystemExit(
                f"publication record lacks a confirmed remote branch for {layer_id}"
            )

    stack_groups: dict[int, str] = {}
    for publication in checkpoint["publications"].values():
        stack_number = publication["stack_number"]
        if stack_number is None:
            continue
        group_id = publication["group"]
        existing_group = stack_groups.setdefault(stack_number, group_id)
        if existing_group != group_id:
            raise SystemExit(
                f"GitHub stack {stack_number} is assigned to multiple publication "
                "groups"
            )


def require_publishing() -> tuple[dict[str, Any], dict[str, Any]]:
    """Return strictly validated publishing checkpoint and plan state."""
    checkpoint = load_checkpoint()
    if checkpoint["phase"] != "publishing":
        raise SystemExit("remote publication actions require the publishing phase")
    validate_push_remote(checkpoint)
    plan = load_plan(checkpoint)
    validate_progress_against_plan(checkpoint, plan)
    return checkpoint, plan


def command_prepare_push(args: argparse.Namespace) -> None:
    """Checkpoint the exact lease before pushing one planned branch."""
    checkpoint, plan = require_publishing()
    _group, layer = find_layer(plan, args.layer)
    if args.layer in checkpoint["publications"]:
        raise SystemExit("cannot prepare a push after recording its review request")
    expected_old: str | None
    if args.expected_old == "absent":
        expected_old = None
    else:
        expected_old = require_string(
            args.expected_old,
            "expected_old",
            pattern=OBJECT_ID,
        )
    proposed = {
        "layer": args.layer,
        "branch": layer["branch"],
        "expected_old": expected_old,
        "planned_head": layer["tip"],
        "prepared_at": now(),
        "confirmed_head": None,
        "confirmed_at": None,
    }
    existing = checkpoint["remote_branches"].get(args.layer)
    if existing is None and expected_old is not None:
        raise SystemExit("a fresh publication branch must be absent on the remote")
    if existing is not None:
        comparable = dict(existing)
        comparable["prepared_at"] = proposed["prepared_at"]
        comparable["confirmed_head"] = None
        comparable["confirmed_at"] = None
        if comparable != proposed:
            raise SystemExit(f"push lease changed for layer {args.layer}")
        print(json.dumps(existing, indent=2, sort_keys=True))
        return
    checkpoint["remote_branches"][args.layer] = proposed
    save_checkpoint(checkpoint)
    print(json.dumps(proposed, indent=2, sort_keys=True))


def command_push_target(args: argparse.Namespace) -> None:
    """Return checkpoint-bound Git arguments for one prepared branch push."""
    checkpoint, plan = require_publishing()
    _group, layer = find_layer(plan, args.layer)
    remote_branch = checkpoint["remote_branches"].get(args.layer)
    if remote_branch is None:
        raise SystemExit(f"prepare the push lease for layer {args.layer} first")
    if remote_branch.get("confirmed_head") is not None:
        raise SystemExit(f"the push for layer {args.layer} is already confirmed")
    branch_ref = f"refs/heads/{layer['branch']}"
    expected_old = remote_branch["expected_old"] or ""
    arguments = [
        "-c",
        "push.pushOption=",
        "push",
        "--no-follow-tags",
        "--recurse-submodules=no",
        f"--force-with-lease={branch_ref}:{expected_old}",
        checkpoint["remote"],
        f"{layer['tip']}:{branch_ref}",
    ]
    output = {
        "arguments": arguments,
        "branch": layer["branch"],
        "expected_old": remote_branch["expected_old"],
        "head_repository": checkpoint["head_repository"],
        "host_url": checkpoint["host_url"],
        "layer": args.layer,
        "planned_head": layer["tip"],
        "provider": checkpoint["provider"],
        "remote": checkpoint["remote"],
        "target_repository": checkpoint["target_repository"],
    }
    if checkpoint["provider"] == "github":
        output["head_repository_id"] = checkpoint["head_repository_id"]
        output["target_repository_id"] = checkpoint["target_repository_id"]
    else:
        output["head_project_id"] = checkpoint["head_project_id"]
        output["target_project_id"] = checkpoint["target_project_id"]
    print(json.dumps(output, indent=2, sort_keys=True))


def command_confirm_push(args: argparse.Namespace) -> None:
    """Record a fetched remote branch at its exact planned commit."""
    checkpoint, plan = require_publishing()
    _group, layer = find_layer(plan, args.layer)
    remote_branch = checkpoint["remote_branches"].get(args.layer)
    if remote_branch is None:
        raise SystemExit(f"prepare the push lease before confirming layer {args.layer}")
    remote_head = require_string(
        args.remote_head,
        "remote_head",
        pattern=OBJECT_ID,
    )
    if remote_head != layer["tip"]:
        raise SystemExit("fetched remote branch does not match its planned head")
    confirmed = remote_branch.get("confirmed_head")
    if confirmed is not None and confirmed != remote_head:
        raise SystemExit(f"confirmed remote branch changed for layer {args.layer}")
    remote_branch["confirmed_head"] = remote_head
    if remote_branch.get("confirmed_at") is None:
        remote_branch["confirmed_at"] = now()
    save_checkpoint(checkpoint)
    print(json.dumps(remote_branch, indent=2, sort_keys=True))


def request_payload_path(run_id: str, layer_id: str, operation: str) -> Path:
    """Return one collision-resistant helper-owned API request path."""
    stem = hashlib.sha256(layer_id.encode("utf-8")).hexdigest()
    return run_dir(run_id) / "requests" / f"{stem}-{operation}.json"


def command_gitlab_request(args: argparse.Namespace) -> None:
    """Generate an exact host-pinned GitLab merge-request creation payload."""
    checkpoint, plan = require_publishing()
    if checkpoint["provider"] != "gitlab":
        raise SystemExit("GitLab request payloads require a GitLab publication")
    group, layer = find_layer(plan, args.layer)
    if args.layer in checkpoint["created_reviews"]:
        raise SystemExit("this merge request is already recorded as created")
    if args.layer in checkpoint["publications"]:
        raise SystemExit("this merge request is already finally recorded")
    remote_branch = checkpoint["remote_branches"].get(args.layer)
    if remote_branch is None or remote_branch.get("confirmed_head") != layer["tip"]:
        raise SystemExit("confirm the remote source branch before creating its request")
    body_file, body_sha256, body = materialize_rendered_body(
        checkpoint,
        group,
        layer,
    )
    payload = {
        "source_branch": layer["branch"],
        "target_branch": layer["base_branch"],
        "title": layer["title"],
        "description": body,
        "target_project_id": checkpoint["target_project_id"],
    }
    payload_file = request_payload_path(checkpoint["run_id"], args.layer, "create")
    write_json(payload_file, payload)
    output = {
        "base_branch": layer["base_branch"],
        "base_head": expected_base_head(checkpoint, plan, layer),
        "body_file": body_file,
        "body_sha256": body_sha256,
        "endpoint": (f"projects/{checkpoint['head_project_id']}/merge_requests"),
        "head_branch": layer["branch"],
        "head_commit": layer["tip"],
        "head_project_id": checkpoint["head_project_id"],
        "head_repository": checkpoint["head_repository"],
        "host_url": checkpoint["host_url"],
        "payload_file": str(payload_file),
        "target_project_id": checkpoint["target_project_id"],
        "target_repository": checkpoint["target_repository"],
    }
    print(json.dumps(output, indent=2, sort_keys=True))


def command_github_request(args: argparse.Namespace) -> None:
    """Generate one checkpoint-bound GitHub pull-request creation payload."""
    checkpoint, plan = require_publishing()
    if checkpoint["provider"] != "github":
        raise SystemExit("GitHub request targets require a GitHub publication")
    group, layer = find_layer(plan, args.layer)
    if args.layer in checkpoint["created_reviews"]:
        raise SystemExit("this pull request is already recorded as created")
    if args.layer in checkpoint["publications"]:
        raise SystemExit("this pull request is already finally recorded")
    remote_branch = checkpoint["remote_branches"].get(args.layer)
    if remote_branch is None or remote_branch.get("confirmed_head") != layer["tip"]:
        raise SystemExit("confirm the remote source branch before creating its request")
    if checkpoint["head_repository"] == checkpoint["target_repository"]:
        head = layer["branch"]
    else:
        head_owner = checkpoint["head_repository"].split("/", maxsplit=1)[0]
        head = f"{head_owner}:{layer['branch']}"
    body_file, body_sha256, body = materialize_rendered_body(
        checkpoint,
        group,
        layer,
    )
    payload = {
        "title": layer["title"],
        "head": head,
        "base": layer["base_branch"],
        "body": body,
        "draft": checkpoint["requested_status"] == "draft",
    }
    if checkpoint["head_repository"] != checkpoint["target_repository"]:
        payload["head_repo"] = checkpoint["head_repository"].split("/", maxsplit=1)[1]
    payload_file = request_payload_path(
        checkpoint["run_id"], args.layer, "github-create"
    )
    write_json(payload_file, payload)
    output = {
        "base_branch": layer["base_branch"],
        "base_head": expected_base_head(checkpoint, plan, layer),
        "body_file": body_file,
        "body_sha256": body_sha256,
        "endpoint": f"repos/{checkpoint['target_repository']}/pulls",
        "head_branch": layer["branch"],
        "head_commit": layer["tip"],
        "head_repository": checkpoint["head_repository"],
        "head_repository_id": checkpoint["head_repository_id"],
        "host_url": checkpoint["host_url"],
        "layer": args.layer,
        "payload_file": str(payload_file),
        "target_repository": checkpoint["target_repository"],
        "target_repository_id": checkpoint["target_repository_id"],
    }
    print(json.dumps(output, indent=2, sort_keys=True))


def command_github_stack_request(args: argparse.Namespace) -> None:
    """Return an exact GitHub stack-creation or adoption request."""
    checkpoint, plan = require_publishing()
    if checkpoint["provider"] != "github":
        raise SystemExit("GitHub stack targets require a GitHub publication")
    group = find_group(plan, args.group)
    if group["transport"] != "github-stack" or len(group["layers"]) < 2:
        raise SystemExit("GitHub stack creation requires a native stack group")
    layer_ids = [layer["id"] for layer in group["layers"]]
    if any(layer_id in checkpoint["publications"] for layer_id in layer_ids):
        raise SystemExit("cannot recreate a group after final review verification")
    missing = [
        layer_id
        for layer_id in layer_ids
        if layer_id not in checkpoint["created_reviews"]
    ]
    if missing:
        raise SystemExit(
            "record every created review before creating the stack: "
            + ", ".join(missing)
        )
    reviews = [checkpoint["created_reviews"][layer_id] for layer_id in layer_ids]
    observations = load_json(Path(args.observations))
    if observations.get("schema") != SCHEMA:
        raise SystemExit("unsupported GitHub stack-membership observation schema")
    pull_requests = require_list(observations.get("pull_requests"), "pull_requests")
    if len(pull_requests) != len(layer_ids):
        raise SystemExit("GitHub stack observations do not cover the planned group")
    memberships: list[dict[str, int] | None] = []
    for position, (raw_observation, layer_id) in enumerate(
        zip(pull_requests, layer_ids, strict=True),
        start=1,
    ):
        observation = require_mapping(
            raw_observation,
            f"pull_requests[{position - 1}]",
        )
        expected_number = checkpoint["created_reviews"][layer_id]["number"]
        number = require_integer(
            observation.get("number"),
            f"pull_requests[{position - 1}].number",
        )
        if number != expected_number:
            raise SystemExit("GitHub stack observations changed pull request order")
        raw_stack = observation.get("stack")
        if raw_stack is None:
            memberships.append(None)
            continue
        stack = require_mapping(raw_stack, f"pull_requests[{position - 1}].stack")
        membership = {
            "number": require_integer(
                stack.get("number"),
                f"pull_requests[{position - 1}].stack.number",
            ),
            "position": require_integer(
                stack.get("position"),
                f"pull_requests[{position - 1}].stack.position",
            ),
            "size": require_integer(
                stack.get("size"),
                f"pull_requests[{position - 1}].stack.size",
            ),
        }
        memberships.append(membership)

    if all(membership is None for membership in memberships):
        action = "create"
        stack_number: int | None = None
    elif any(membership is None for membership in memberships):
        raise SystemExit("planned pull requests have partial GitHub stack membership")
    else:
        stacked = [membership for membership in memberships if membership is not None]
        stack_numbers = {membership["number"] for membership in stacked}
        sizes = {membership["size"] for membership in stacked}
        positions = [membership["position"] for membership in stacked]
        if (
            len(stack_numbers) != 1
            or sizes != {len(layer_ids)}
            or positions != list(range(1, len(layer_ids) + 1))
        ):
            raise SystemExit(
                "planned pull requests belong to a foreign or differently ordered "
                "GitHub stack"
            )
        action = "adopt"
        stack_number = next(iter(stack_numbers))
    output = {
        "action": action,
        "group": group["id"],
        "head_repository": checkpoint["head_repository"],
        "head_repository_id": checkpoint["head_repository_id"],
        "host_url": checkpoint["host_url"],
        "pull_requests": [
            {
                "base": review["base"],
                "base_head": review["base_head"],
                "body_file": review["body_file"],
                "body_path": str(run_dir(checkpoint["run_id"]) / review["body_file"]),
                "body_sha256": review["body_sha256"],
                "branch": review["branch"],
                "head": review["head"],
                "number": review["number"],
                "stack": memberships[position],
                "status": review["status"],
                "title": group["layers"][position]["title"],
                "url": review["url"],
            }
            for position, review in enumerate(reviews)
        ],
        "target_repository": checkpoint["target_repository"],
        "target_repository_id": checkpoint["target_repository_id"],
    }
    if action == "create":
        payload_file = request_payload_path(
            checkpoint["run_id"],
            group["id"],
            "github-stack-create",
        )
        write_json(
            payload_file,
            {"pull_requests": [review["number"] for review in reviews]},
        )
        output["endpoint"] = f"repos/{checkpoint['target_repository']}/stacks"
        output["payload_file"] = str(payload_file)
    if stack_number is not None:
        output["stack_number"] = stack_number
    print(json.dumps(output, indent=2, sort_keys=True))


def command_advance(args: argparse.Namespace) -> None:
    """Advance through a non-remote publication phase."""
    checkpoint = load_checkpoint()
    transitions = {
        "planned": "validated",
        "validated": "publishing",
    }
    expected = transitions.get(checkpoint["phase"])
    if expected != args.phase:
        raise SystemExit(
            f"cannot advance publication from {checkpoint['phase']} to {args.phase}"
        )
    load_plan(checkpoint)
    checkpoint["phase"] = args.phase
    save_checkpoint(checkpoint)
    print(json.dumps(checkpoint, indent=2, sort_keys=True))


def verified_review_identity(
    args: argparse.Namespace,
    checkpoint: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Validate provider observations and return one planned review identity."""
    group, layer = find_layer(plan, args.layer)
    remote_branch = checkpoint["remote_branches"].get(args.layer)
    if remote_branch is None or remote_branch.get("confirmed_head") != layer["tip"]:
        raise SystemExit("recording a review requires its confirmed remote branch")
    head = canonical_commit(args.head)
    if head != layer["tip"]:
        raise SystemExit("published review head does not match its planned tip")
    if args.base != layer["base_branch"]:
        raise SystemExit("published review base does not match its plan")
    if args.status != checkpoint["requested_status"]:
        raise SystemExit("published review status does not match the requested status")
    base_head = canonical_commit(args.base_head)
    if base_head != expected_base_head(checkpoint, plan, layer):
        raise SystemExit("published review base head does not match its planned commit")
    number = require_integer(args.number, "number")
    url = require_string(args.url, "url")
    expected_url = canonical_review_url(
        checkpoint["provider"],
        checkpoint["host_url"],
        checkpoint["target_repository"],
        number,
    )
    if url.rstrip("/") != expected_url:
        raise SystemExit("published review URL does not match the target repository")
    identity = {
        "group": group["id"],
        "layer": args.layer,
        "branch": layer["branch"],
        "base": args.base,
        "base_head": base_head,
        "head": head,
        "number": number,
        "url": expected_url,
        "status": args.status,
    }
    return group, layer, identity


def reject_duplicate_review_identity(
    records: dict[str, Any],
    layer_id: str,
    identity: dict[str, Any],
) -> None:
    """Reject one number or URL already bound to a different layer."""
    for existing_layer, existing_record in records.items():
        if existing_layer == layer_id:
            continue
        if existing_record["number"] == identity["number"]:
            raise SystemExit(
                f"review request {identity['number']} is already assigned to another layer"
            )
        if existing_record["url"] == identity["url"]:
            raise SystemExit("review URL is already assigned to another layer")


def command_record_created_review(args: argparse.Namespace) -> None:
    """Checkpoint one verified review identity immediately after creation."""
    checkpoint, plan = require_publishing()
    if args.layer in checkpoint["publications"]:
        raise SystemExit("this review request is already finally recorded")
    group, layer, identity = verified_review_identity(args, checkpoint, plan)
    created_reviews = checkpoint["created_reviews"]
    reject_duplicate_review_identity(created_reviews, args.layer, identity)
    existing = created_reviews.get(args.layer)
    if existing is not None:
        if any(existing.get(field) != expected for field, expected in identity.items()):
            raise SystemExit(f"created review identity changed for layer {args.layer}")
        print(json.dumps(existing, indent=2, sort_keys=True))
        return
    body_file, body_sha256, _body = materialize_rendered_body(
        checkpoint,
        group,
        layer,
    )
    created_review = {
        **identity,
        "body_file": body_file,
        "body_sha256": body_sha256,
        "observed_at": now(),
    }
    created_reviews[args.layer] = created_review
    save_checkpoint(checkpoint)
    print(json.dumps(created_review, indent=2, sort_keys=True))


def command_record_review(args: argparse.Namespace) -> None:
    """Record one final verified remote pull or merge request."""
    checkpoint, plan = require_publishing()
    group, _layer, identity = verified_review_identity(args, checkpoint, plan)
    created_review = checkpoint["created_reviews"].get(args.layer)
    if created_review is None:
        raise SystemExit("record the created review identity before final verification")
    for field, expected in identity.items():
        if created_review.get(field) != expected:
            raise SystemExit(f"final review identity changed for layer {args.layer}")
    if group["transport"] == "github-stack" and len(group["layers"]) > 1:
        stack_number: int | None = require_integer(args.stack_number, "stack_number")
    else:
        if args.stack_number is not None:
            raise SystemExit("this publication transport cannot record a stack number")
        stack_number = None
    publications = checkpoint["publications"]
    if stack_number is not None:
        for existing_publication in publications.values():
            if (
                existing_publication["stack_number"] == stack_number
                and existing_publication["group"] != group["id"]
            ):
                raise SystemExit(
                    f"GitHub stack {stack_number} is already assigned to another "
                    "publication group"
                )
    reject_duplicate_review_identity(publications, args.layer, identity)
    existing = publications.get(args.layer)
    if existing is not None:
        expected_existing = {**identity, "stack_number": stack_number}
        if any(
            existing.get(field) != expected
            for field, expected in expected_existing.items()
        ):
            raise SystemExit(f"publication record changed for layer {args.layer}")
        print(json.dumps(existing, indent=2, sort_keys=True))
        return
    publication = {
        **identity,
        "stack_number": stack_number,
        "verified_at": now(),
    }
    publications[args.layer] = publication
    save_checkpoint(checkpoint)
    print(json.dumps(publication, indent=2, sort_keys=True))


def command_finish(_args: argparse.Namespace) -> None:
    """Mark a fully recorded publication run complete."""
    checkpoint = load_checkpoint()
    if checkpoint["phase"] != "publishing":
        raise SystemExit("publication can finish only from the publishing phase")
    plan = load_plan(checkpoint)
    validate_progress_against_plan(checkpoint, plan)
    expected_layers = {
        layer["id"] for group in plan["groups"] for layer in group["layers"]
    }
    if set(checkpoint["created_reviews"]) != expected_layers:
        missing = sorted(expected_layers - set(checkpoint["created_reviews"]))
        raise SystemExit("created review records are incomplete: " + ", ".join(missing))
    if set(checkpoint["publications"]) != expected_layers:
        missing = sorted(expected_layers - set(checkpoint["publications"]))
        raise SystemExit("publication records are incomplete: " + ", ".join(missing))
    for group in plan["groups"]:
        if group["transport"] != "github-stack" or len(group["layers"]) < 2:
            continue
        stack_numbers = {
            checkpoint["publications"][layer["id"]]["stack_number"]
            for layer in group["layers"]
        }
        if len(stack_numbers) != 1:
            raise SystemExit(
                f"GitHub stack group {group['id']} has inconsistent stack numbers"
            )
    checkpoint["phase"] = "published"
    save_checkpoint(checkpoint)
    print(json.dumps(checkpoint, indent=2, sort_keys=True))


def validate_resume_state(checkpoint: dict[str, Any]) -> dict[str, Any]:
    """Validate local recovery and planned branches before resuming."""
    recovery = git_run(
        "show-ref", "--verify", "--hash", checkpoint["recovery_ref"], check=False
    )
    if recovery.returncode or recovery.stdout.strip() != checkpoint["source_head"]:
        raise SystemExit("publication recovery ref is missing or changed")
    canonical_commit(checkpoint["target_base"])
    commits = range_commits(checkpoint["base"], checkpoint["recovery_ref"])
    if commits != checkpoint["commits"]:
        raise SystemExit("publication recovery range does not match its checkpoint")
    if checkpoint["phase"] != "published":
        validate_push_remote(checkpoint)
    if checkpoint["phase"] != "started":
        plan = load_plan(
            checkpoint,
            check_branches=checkpoint["phase"] != "published",
        )
        validate_progress_against_plan(checkpoint, plan)
    return checkpoint


def command_status(args: argparse.Namespace) -> None:
    """Print the active state after strict validation."""
    checkpoint = validate_resume_state(load_checkpoint())
    if args.json:
        print(json.dumps(checkpoint, indent=2, sort_keys=True))
        return
    print(f"run: {checkpoint['run_id']}")
    print(f"phase: {checkpoint['phase']}")
    print(f"requested status: {checkpoint['requested_status']}")
    print(f"recovery ref: {checkpoint['recovery_ref']}")


def command_state_dir(_args: argparse.Namespace) -> None:
    """Print the private state root."""
    print(STATE_ROOT)


def command_run_dir(_args: argparse.Namespace) -> None:
    """Print the active run directory."""
    run_id = active_run_id()
    assert run_id is not None
    print(run_dir(run_id))


def parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    root = argparse.ArgumentParser(description=__doc__)
    commands = root.add_subparsers(dest="command", required=True)

    state_dir_parser = commands.add_parser("state-dir")
    state_dir_parser.set_defaults(func=command_state_dir)

    run_dir_parser = commands.add_parser("run-dir")
    run_dir_parser.set_defaults(func=command_run_dir)

    start = commands.add_parser("start")
    start.add_argument("--base", required=True)
    start.add_argument("--target-base", required=True)
    start.add_argument("--provider", choices=PROVIDERS, required=True)
    start.add_argument("--host-url", required=True)
    start.add_argument("--target-repository", required=True)
    start.add_argument("--head-repository", required=True)
    start.add_argument("--target-repository-id", type=int)
    start.add_argument("--head-repository-id", type=int)
    start.add_argument("--target-project-id", type=int)
    start.add_argument("--head-project-id", type=int)
    start.add_argument("--remote", required=True)
    start.add_argument("--head-push-url", action="append")
    start.add_argument("--trunk", required=True)
    start.add_argument("--status", choices=PUBLICATION_STATUSES, required=True)
    start.set_defaults(func=command_start)

    record_normalized = commands.add_parser("record-normalized")
    record_normalized.add_argument("--tip", required=True)
    record_normalized.set_defaults(func=command_record_normalized)

    refresh_normalized = commands.add_parser("refresh-normalized")
    refresh_normalized.add_argument("--target-base", required=True)
    refresh_normalized.add_argument("--tip", required=True)
    refresh_normalized.set_defaults(func=command_refresh_normalized)

    record_plan = commands.add_parser("record-plan")
    record_plan.add_argument("--file", required=True)
    record_plan.set_defaults(func=command_record_plan)

    advance = commands.add_parser("advance")
    advance.add_argument("--phase", choices=("validated", "publishing"), required=True)
    advance.set_defaults(func=command_advance)

    prepare_push = commands.add_parser("prepare-push")
    prepare_push.add_argument("--layer", required=True)
    prepare_push.add_argument("--expected-old", required=True)
    prepare_push.set_defaults(func=command_prepare_push)

    push_target = commands.add_parser("push-target")
    push_target.add_argument("--layer", required=True)
    push_target.set_defaults(func=command_push_target)

    confirm_push = commands.add_parser("confirm-push")
    confirm_push.add_argument("--layer", required=True)
    confirm_push.add_argument("--remote-head", required=True)
    confirm_push.set_defaults(func=command_confirm_push)

    gitlab_request = commands.add_parser("gitlab-request")
    gitlab_request.add_argument("--layer", required=True)
    gitlab_request.set_defaults(func=command_gitlab_request)

    github_request = commands.add_parser("github-request")
    github_request.add_argument("--layer", required=True)
    github_request.set_defaults(func=command_github_request)

    github_stack_request = commands.add_parser("github-stack-request")
    github_stack_request.add_argument("--group", required=True)
    github_stack_request.add_argument("--observations", required=True)
    github_stack_request.set_defaults(func=command_github_stack_request)

    record_created_review = commands.add_parser("record-created-review")
    record_created_review.add_argument("--layer", required=True)
    record_created_review.add_argument("--number", type=int, required=True)
    record_created_review.add_argument("--url", required=True)
    record_created_review.add_argument("--head", required=True)
    record_created_review.add_argument("--base", required=True)
    record_created_review.add_argument("--base-head", required=True)
    record_created_review.add_argument(
        "--status", choices=PUBLICATION_STATUSES, required=True
    )
    record_created_review.set_defaults(func=command_record_created_review)

    record_review = commands.add_parser("record-review")
    record_review.add_argument("--layer", required=True)
    record_review.add_argument("--number", type=int, required=True)
    record_review.add_argument("--url", required=True)
    record_review.add_argument("--head", required=True)
    record_review.add_argument("--base", required=True)
    record_review.add_argument("--base-head", required=True)
    record_review.add_argument("--status", choices=PUBLICATION_STATUSES, required=True)
    record_review.add_argument("--stack-number", type=int)
    record_review.set_defaults(func=command_record_review)

    finish = commands.add_parser("finish")
    finish.set_defaults(func=command_finish)

    status = commands.add_parser("status")
    status.add_argument("--json", action="store_true")
    status.set_defaults(func=command_status)
    return root


def main() -> None:
    """Run the selected helper command."""
    args = parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
