#!/usr/bin/env python3
"""Run grouped checks in one clean snapshot and retain content-bound evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import TypeAlias


JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def canonical(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def git(repo: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(repo), "--no-optional-locks", *args], text=True
    ).strip()


def check_spec(value: dict[str, JsonValue]) -> None:
    for key in ("commands", "setup"):
        commands = value.get(key, [])
        if not isinstance(commands, list) or (key == "commands" and not commands):
            raise SystemExit(f"{key} must be a list of command argument lists")
        for command in commands:
            if (
                not isinstance(command, list)
                or not command
                or not all(isinstance(a, str) and a for a in command)
            ):
                raise SystemExit(f"invalid command in {key}")
    if not isinstance(value.get("environment", {}), dict) or not all(
        isinstance(k, str) and isinstance(v, str)
        for k, v in value.get("environment", {}).items()
    ):
        raise SystemExit("environment must map names to string values")
    if not isinstance(value.get("inputs", []), list) or not all(
        isinstance(p, str) for p in value.get("inputs", [])
    ):
        raise SystemExit("inputs must list external prerequisite paths")
    if not isinstance(value.get("prerequisites", {}), dict):
        raise SystemExit(
            "prerequisites must describe runtime and dependency identities"
        )


def identity(
    repo: Path, commit: str, spec: dict[str, JsonValue], environment: dict[str, str]
) -> dict[str, JsonValue]:
    tools = {}
    for command in spec.get("setup", []) + spec["commands"]:
        executable = command[0]
        if "/" in executable and not Path(executable).is_absolute():
            if ".." in Path(executable).parts:
                raise SystemExit(
                    "relative executable must stay inside the snapshot; use an absolute path for external tools"
                )
            tools[executable] = {"snapshot_path": executable}
            continue
        resolved = shutil.which(executable, path=environment.get("PATH"))
        if resolved is None:
            raise SystemExit(f"missing executable: {executable}")
        path = Path(resolved).resolve()
        if executable not in tools:
            tools[executable] = {"path": str(path), "sha256": file_digest(path)}
    inputs = {}
    for name in spec.get("inputs", []):
        path = Path(name).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"external prerequisite must be a file: {name}")
        inputs[str(path)] = file_digest(path)
    return {
        "tree": git(repo, "rev-parse", f"{commit}^{{tree}}"),
        "spec": spec,
        "environment_sha256": digest(canonical(environment)),
        "tools": tools,
        "inputs": inputs,
        "runner_sha256": file_digest(Path(__file__)),
    }


def unchanged(repo: Path, commit: str) -> bool:
    if git(repo, "rev-parse", "HEAD") != commit:
        return False
    for args in (("diff", "--quiet", "HEAD"), ("diff", "--cached", "--quiet")):
        if subprocess.run(
            ["git", "-C", str(repo), "--no-optional-locks", *args], check=False
        ).returncode:
            return False
    return not git(repo, "ls-files", "--others", "--exclude-standard")


def run_checks(
    repo: Path,
    commit: str,
    spec: dict[str, JsonValue],
    environment: dict[str, JsonValue],
    attempt: Path,
) -> list[dict[str, JsonValue]]:
    results = []
    scratch = next(
        (os.environ[k] for k in ("TMPDIR", "TEMP", "TMP") if os.environ.get(k)), None
    )
    if scratch is None and sys.platform == "linux":
        scratch = "/var/tmp"
    with tempfile.TemporaryDirectory(
        prefix="decompose-verify-", dir=scratch
    ) as temporary:
        worktree = Path(temporary) / "worktree"
        try:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "--no-optional-locks",
                    "worktree",
                    "add",
                    "--detach",
                    str(worktree),
                    commit,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            for kind in ("setup", "commands"):
                for command in spec.get(kind, []):
                    log = attempt / f"{len(results)}.log"
                    with log.open("wb") as handle:
                        try:
                            code = subprocess.run(
                                command,
                                cwd=worktree,
                                env=environment,
                                stdout=handle,
                                stderr=subprocess.STDOUT,
                                check=False,
                            ).returncode
                        except OSError as error:
                            handle.write(str(error).encode())
                            code = 127
                    source_unchanged = unchanged(worktree, commit)
                    results.append(
                        {
                            "kind": kind,
                            "command": command,
                            "exit_code": code,
                            "source_unchanged": source_unchanged,
                            "log": log.name,
                            "log_sha256": file_digest(log),
                        }
                    )
                    if code != 0 or not source_unchanged:
                        return results
        finally:
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "--no-optional-locks",
                    "worktree",
                    "remove",
                    "--force",
                    str(worktree),
                ],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument(
        "--checks",
        type=Path,
        help="JSON commands, setup, environment, inputs, and prerequisite identities",
    )
    parser.add_argument(
        "--evidence-dir", type=Path, help="retain immutable attempts and receipts here"
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if args.checks and command:
        parser.error("choose --checks or a single command")
    spec = (
        json.loads(args.checks.read_text())
        if args.checks
        else {
            "commands": [
                command or [sys.executable, "-m", "compileall", "-q", "src", "tests"]
            ]
        }
    )
    check_spec(spec)
    repo = Path(args.repo).resolve()
    commit = git(repo, "rev-parse", "--verify", f"{args.ref}^{{commit}}")
    environment = {**os.environ, **spec.get("environment", {})}
    expected = identity(repo, commit, spec, environment)
    key = digest(canonical(expected))
    with tempfile.TemporaryDirectory(prefix="decompose-check-logs-") as temporary:
        root = (
            args.evidence_dir.resolve() / key if args.evidence_dir else Path(temporary)
        )
        attempt = root / f"{time.time_ns()}-{uuid.uuid4().hex}"
        attempt.mkdir(parents=True)
        results = run_checks(repo, commit, spec, environment, attempt)
        identity_unchanged = identity(repo, commit, spec, environment) == expected
        passed = identity_unchanged and all(
            r["exit_code"] == 0 and r["source_unchanged"] for r in results
        )
        receipt = {
            "schema": 1,
            "commit": commit,
            "identity": expected,
            "identity_unchanged": identity_unchanged,
            "passed": passed,
            "results": results,
            "retry_reason": None,
        }
        path = attempt / "receipt.json"
        path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
        if not args.evidence_dir and not passed:
            for result in results:
                print(
                    (attempt / result["log"]).read_text(errors="replace"),
                    file=sys.stderr,
                )
            if not all(r["source_unchanged"] for r in results):
                print(
                    "check altered source or introduced nonignored files",
                    file=sys.stderr,
                )
        print(
            json.dumps(
                {
                    "commit": commit,
                    "tree": expected["tree"],
                    "receipt": str(path) if args.evidence_dir else None,
                    "reused": False,
                    "passed": passed,
                }
            )
        )
        return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
