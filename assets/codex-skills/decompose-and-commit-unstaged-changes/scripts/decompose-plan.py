#!/usr/bin/env python3
"""Capture unstaged input and validate preparation plans without constructing history."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import uuid
from pathlib import Path
from typing import TypeAlias


JsonValue: TypeAlias = (
    str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
)


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.check_output(
        ["git", "-C", str(repo), "--no-optional-locks", *args], input=data
    )


def tree(repo: Path, ref: str) -> dict[str, dict[str, str]]:
    entries = {}
    for record in git(repo, "ls-tree", "-rz", "--full-tree", ref).split(b"\0"):
        if record:
            metadata, path = record.split(b"\t", 1)
            mode, _kind, oid = metadata.decode().split()
            entries[os.fsdecode(path)] = {"mode": mode, "object": oid}
    return entries


def file_objects(repo: Path, names: list[str]) -> dict[str, str]:
    """Hash ordinary files in one read-only Git call, honoring clean filters."""
    if not names:
        return {}
    # --stdin-paths accepts Git's C-quoted names, including embedded newlines.
    quoted = []
    for name in names:
        parts = []
        for byte in os.fsencode(name):
            if byte in (34, 92):
                parts.append(b"\\" + bytes((byte,)))
            elif 32 <= byte < 127:
                parts.append(bytes((byte,)))
            else:
                parts.append(f"\\{byte:03o}".encode())
        quoted.append(b'"' + b"".join(parts) + b'"\n')
    objects = (
        git(repo, "hash-object", "--stdin-paths", data=b"".join(quoted))
        .decode()
        .splitlines()
    )
    require(len(objects) == len(names), "Git did not hash the complete input inventory")
    return dict(zip(names, objects))


def state_path(repo: Path) -> Path:
    path = Path(os.environ.get("DECOMPOSE_STATE_DIR", str(repo / ".git-stage-batch")))
    require(not path.is_symlink(), "workflow state directory must not be a symlink")
    return path.expanduser().resolve()


def inventory(
    repo: Path, base: str, state: Path
) -> tuple[dict[str, JsonValue], dict[str, bytes]]:
    baseline = tree(repo, base)
    filemode = (
        subprocess.run(
            [
                "git",
                "-C",
                str(repo),
                "--no-optional-locks",
                "config",
                "--bool",
                "--get",
                "core.filemode",
            ],
            capture_output=True,
            check=False,
        ).stdout.strip()
        != b"false"
    )
    index = git(repo, "ls-files", "--stage", "-z")
    index_modes, index_objects = {}, {}
    for record in index.split(b"\0"):
        if record:
            metadata, path = record.split(b"\t", 1)
            mode, oid, stage = metadata.decode().split()
            require(stage == "0", "resolve index conflicts before decomposition")
            index_modes[os.fsdecode(path)] = mode
            index_objects[os.fsdecode(path)] = oid
    others = git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    paths = (
        set(baseline)
        | set(index_modes)
        | {os.fsdecode(path) for path in others.split(b"\0") if path}
    )
    ordinary = [
        name
        for name in sorted(paths)
        if index_modes.get(name, baseline.get(name, {}).get("mode")) != "160000"
        and (repo / name).is_file()
        and not (repo / name).is_symlink()
        and repo / name != state
        and state not in (repo / name).parents
    ]
    objects = file_objects(repo, ordinary)
    entries, contents = {}, {}
    for name in sorted(paths):
        path = repo / name
        # Compare lexical paths: a tracked symlink remains an owned entry.
        if path == state or state in path.parents:
            continue
        mode = index_modes.get(name, baseline.get(name, {}).get("mode"))
        if mode == "160000":
            oid = index_objects.get(name, baseline.get(name, {}).get("object"))
            if os.path.lexists(path / ".git"):
                oid = git(path, "rev-parse", "HEAD").decode().strip()
                require(
                    not git(path, "status", "--porcelain"), f"dirty submodule: {name}"
                )
            entries[name] = {"mode": mode, "object": oid, "raw_sha256": oid}
            continue
        if not os.path.lexists(path):
            continue
        if path.is_symlink():
            data = os.fsencode(os.readlink(path))
            mode = "120000"
            oid = git(repo, "hash-object", "--stdin", data=data).decode().strip()
        else:
            require(path.is_file(), f"unsupported input path: {name}")
            data = path.read_bytes()
            mode = "100755" if path.stat().st_mode & stat.S_IXUSR else "100644"
            if not filemode:
                mode = index_modes.get(name, mode)
            oid = objects[name]
        entries[name] = {"mode": mode, "object": oid, "raw_sha256": digest(data)}
        if {"mode": mode, "object": oid} != baseline.get(name):
            contents[name] = data
    identity = {
        "head": git(repo, "rev-parse", "HEAD").decode().strip(),
        "index_sha256": digest(index),
        "batch_refs_sha256": digest(
            git(
                repo,
                "for-each-ref",
                "--format=%(refname) %(objectname)",
                "refs/git-stage-batch/state",
                "refs/git-stage-batch/batches",
            )
        ),
        "entries": entries,
    }
    return identity, contents


def capture(repo: Path, state: Path, revision: str) -> str:
    base = git(repo, "rev-parse", "--verify", f"{revision}^{{commit}}").decode().strip()
    require(
        git(repo, "rev-parse", "HEAD").decode().strip() == base,
        "capture must start at the current base",
    )
    require(
        not git(repo, "diff", "--cached", "--raw"),
        "capture requires an empty staged diff",
    )
    identity, contents = inventory(repo, base, state)
    baseline = tree(repo, base)
    target = {
        name: {k: entry[k] for k in ("mode", "object")}
        for name, entry in identity["entries"].items()
    }
    changed = sorted(
        name
        for name in baseline.keys() | target.keys()
        if baseline.get(name) != target.get(name)
    )
    require(bool(changed), "no unstaged input to decompose")
    directory = state / f"decompose-input-{uuid.uuid4().hex}"
    directory.mkdir(parents=True)
    for index, name in enumerate(changed):
        if name in contents:
            (directory / f"{index}.blob").write_bytes(contents[name])
    (directory / "tracked.patch").write_bytes(
        git(
            repo,
            "diff",
            "--binary",
            "--full-index",
            "--no-ext-diff",
            "--no-textconv",
            base,
        )
    )
    manifest = {
        "schema": 1,
        "base": base,
        "changed_paths": changed,
        "identity": identity,
    }
    # Capture is observational: refuse a result if input changed during the read.
    require(
        inventory(repo, base, state)[0] == identity,
        "input changed during capture; capture again",
    )
    (directory / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    return str((directory / "manifest.json").relative_to(state))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--base", default="HEAD")
    args = parser.parse_args()
    repo = Path(
        git(Path(args.repo), "rev-parse", "--show-toplevel").decode().strip()
    ).resolve()
    state = state_path(repo)
    if args.command == "capture":
        print(capture(repo, state, args.base))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
