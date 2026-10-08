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


def artifact(state: Path, name: str) -> Path:
    require(isinstance(name, str) and bool(name), "missing artifact path")
    path = (state / name).resolve()
    require(
        path.is_relative_to(state) and path.is_file(),
        f"missing or external artifact: {name}",
    )
    return path


def load_input(state: Path, name: str) -> tuple[dict[str, JsonValue], str]:
    data = artifact(state, name).read_bytes()
    manifest = json.loads(data)
    require(manifest.get("schema") == 1, "unsupported input manifest")
    return manifest, digest(data)


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


def validate(
    repo: Path, state: Path, path: Path
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    plan = json.loads(path.read_text())
    require(
        plan.get("schema") == 2,
        "use preparation plan schema 2; audit older snapshot plans before reuse",
    )
    manifest, input_digest = load_input(state, plan["input_manifest"])
    require(
        plan["input_digest"] == input_digest,
        "plan input digest differs from its manifest",
    )
    base = plan["base"]
    require(
        base
        == manifest["base"]
        == git(repo, "rev-parse", "--verify", f"{base}^{{commit}}").decode().strip(),
        "plan base differs from captured base",
    )
    concerns = plan["concerns"]
    require(isinstance(concerns, list) and bool(concerns), "empty concern list")
    numbers = list(range(1, len(concerns) + 1))
    require(
        [c["number"] for c in concerns] == numbers
        and all(type(c["number"]) is int for c in concerns),
        "noncontiguous concern numbers",
    )
    require(
        plan["peel_order"] == numbers and plan["rebuild_order"] == numbers[::-1],
        "invalid concern orders",
    )
    for key in ("slug", "name"):
        values = [c[key] for c in concerns]
        require(
            all(isinstance(v, str) and v.strip() for v in values)
            and len(set(values)) == len(values),
            f"invalid or duplicate {key}",
        )
    ladder = plan["evolution_ladder"]
    require(
        bool(ladder) and [s["step"] for s in ladder] == list(range(1, len(ladder) + 1)),
        "invalid evolution ladder",
    )
    require(
        all(
            isinstance(s["behavior_after"], str) and s["behavior_after"].strip()
            for s in ladder
        ),
        "missing milestone behavior",
    )
    previous_step = 0
    previous_commit = None
    previous_role = None
    commit_ids = set()
    for concern in reversed(concerns):
        step = concern["evolution_step"]
        require(
            type(step) is int and previous_step <= step <= len(ladder) and step > 0,
            "concerns do not follow milestone order",
        )
        previous_step = step
        require(
            isinstance(concern["purpose"], str) and bool(concern["purpose"].strip()),
            "empty concern purpose",
        )
        deps = concern["depends_on"]
        require(
            isinstance(deps, list)
            and all(
                type(d) is int and concern["number"] < d <= len(concerns) for d in deps
            )
            and len(deps) == len(set(deps)),
            "invalid dependency order",
        )
        evidence = concern["dependency_evidence"]
        require(
            set(deps) == {e["provider"] for e in evidence},
            "missing dependency evidence",
        )
        require(
            all(e.get("anchor") and e.get("contract") for e in evidence),
            "missing dependency anchor or contract",
        )
        commits = concern["expected_commits"]
        require(
            isinstance(commits, list) and bool(commits),
            "missing proposed atomic slices",
        )
        for commit in commits:
            require(
                all(
                    isinstance(commit.get(k), str) and commit[k].strip()
                    for k in ("slug", "purpose")
                ),
                "missing atomic slice purpose or slug",
            )
            require(
                commit.get("role")
                in ("implementation", "verification", "documentation", "mechanical"),
                "invalid atomic slice role",
            )
            identity = f"{concern['slug']}/{commit['slug']}"
            require(identity not in commit_ids, "duplicate atomic slice")
            commit_ids.add(identity)
            if commit.get("role") == "verification":
                validates = commit.get("validates", "")
                existing = (
                    isinstance(validates, str)
                    and validates.startswith("base:")
                    and bool(validates[5:].strip())
                )
                require(
                    existing
                    or (
                        previous_role == "implementation"
                        and validates == previous_commit
                    ),
                    "proof must immediately follow the code it validates, or identify an existing base contract",
                )
            previous_commit = identity
            previous_role = commit["role"]
    ledger = plan["ownership_ledger"]
    require(isinstance(ledger, list) and bool(ledger), "missing ownership ledger")
    owned = set()
    owners = set()
    anchors = set()
    for entry in ledger:
        require(
            isinstance(entry["path"], str) and bool(entry["path"].strip()),
            "missing ownership path",
        )
        require(
            type(entry["concern"]) is int and entry["concern"] in numbers,
            "unknown ledger owner",
        )
        require(
            isinstance(entry["anchor"], str) and bool(entry["anchor"].strip()),
            "missing stable ownership anchor",
        )
        require(entry["kind"] in ("owned", "context"), "unknown ownership kind")
        if entry["kind"] == "owned":
            key = (entry["path"], entry["anchor"])
            require(key not in anchors, "duplicate owned region")
            anchors.add(key)
            owned.add(entry["path"])
            owners.add(entry["concern"])
    require(
        owned == set(manifest["changed_paths"]),
        "owned paths do not cover captured changes exactly",
    )
    require(owners == set(numbers), "each concern must own an actual changed region")
    return plan, manifest


def affected(
    old: dict[str, JsonValue],
    new: dict[str, JsonValue],
    old_input: dict[str, JsonValue],
    new_input: dict[str, JsonValue],
) -> list[str]:
    """Find changed boundaries, order inversions, and dependent/shared-file successors."""
    old_cs = {c["slug"]: c for c in old["concerns"]}
    new_cs = {c["slug"]: c for c in new["concerns"]}

    def signature(
        plan: dict[str, JsonValue], concern: dict[str, JsonValue]
    ) -> dict[str, JsonValue]:
        numbers = {c["number"]: c["slug"] for c in plan["concerns"]}
        result = {
            k: v
            for k, v in concern.items()
            if k
            not in (
                "number",
                "name",
                "evolution_step",
                "depends_on",
                "dependency_evidence",
            )
        }
        result["milestone"] = {
            k: v
            for k, v in plan["evolution_ladder"][concern["evolution_step"] - 1].items()
            if k != "step"
        }
        result["depends_on"] = sorted(numbers[n] for n in concern["depends_on"])
        result["dependency_evidence"] = [
            {**e, "provider": numbers[e["provider"]]}
            for e in concern["dependency_evidence"]
        ]
        result["ownership"] = [
            {k: v for k, v in e.items() if k != "concern"}
            for e in plan["ownership_ledger"]
            if e["concern"] == concern["number"]
        ]
        return result

    changed = {
        s
        for s in old_cs.keys() | new_cs.keys()
        if s not in old_cs
        or s not in new_cs
        or signature(old, old_cs[s]) != signature(new, new_cs[s])
    }
    if old["base"] != new["base"]:
        changed.update(old_cs.keys() | new_cs.keys())
    old_entries, new_entries = (
        old_input["identity"]["entries"],
        new_input["identity"]["entries"],
    )
    changed_paths = {
        p
        for p in old_entries.keys() | new_entries.keys()
        if old_entries.get(p) != new_entries.get(p)
    }
    old_positions = {c["slug"]: i for i, c in enumerate(reversed(old["concerns"]))}
    new_order = [
        c["slug"] for c in reversed(new["concerns"]) if c["slug"] in old_positions
    ]
    for i, slug in enumerate(new_order):
        for later in new_order[i + 1 :]:
            if old_positions[slug] > old_positions[later]:
                changed.update((slug, later))
    consumers = {}
    for plan in (old, new):
        numbers = {c["number"]: c["slug"] for c in plan["concerns"]}
        paths = {
            c["slug"]: {
                e["path"]
                for e in plan["ownership_ledger"]
                if e["concern"] == c["number"]
            }
            for c in plan["concerns"]
        }
        changed.update(s for s, ps in paths.items() if ps & changed_paths)
        earlier = []
        for concern in reversed(plan["concerns"]):
            slug = concern["slug"]
            providers = {numbers[d] for d in concern["depends_on"]}
            providers.update(s for s in earlier if paths[s] & paths[slug])
            for provider in providers:
                consumers.setdefault(provider, set()).add(slug)
            earlier.append(slug)
    pending = list(changed)
    while pending:
        for consumer in consumers.get(pending.pop(), set()) - changed:
            changed.add(consumer)
            pending.append(consumer)
    return [c["slug"] for c in reversed(new["concerns"]) if c["slug"] in changed]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="command", required=True)
    capture_parser = sub.add_parser("capture")
    capture_parser.add_argument("--base", default="HEAD")
    for command in ("verify-input", "verify-target"):
        child = sub.add_parser(command)
        child.add_argument("manifest")
        if command == "verify-target":
            child.add_argument("--ref", default="HEAD")
    sub.add_parser("validate").add_argument("plan", type=Path)
    diff_parser = sub.add_parser("affected")
    diff_parser.add_argument("old", type=Path)
    diff_parser.add_argument("new", type=Path)
    args = parser.parse_args()
    repo = Path(
        git(Path(args.repo), "rev-parse", "--show-toplevel").decode().strip()
    ).resolve()
    state = state_path(repo)
    if args.command == "capture":
        print(capture(repo, state, args.base))
    elif args.command == "validate":
        plan, _manifest = validate(repo, state, args.plan)
        print(
            f"Preparation structure valid: {len(plan['concerns'])} concerns; semantic review still required."
        )
    elif args.command == "affected":
        old, old_input = validate(repo, state, args.old)
        new, new_input = validate(repo, state, args.new)
        print(json.dumps(affected(old, new, old_input, new_input)))
    else:
        manifest, _digest = load_input(state, args.manifest)
        if args.command == "verify-input":
            require(
                inventory(repo, manifest["base"], state)[0] == manifest["identity"],
                "source, index, HEAD, or batch refs changed since input capture",
            )
            print("Captured source, index, HEAD, and batch refs are unchanged.")
        else:
            expected = {
                p: {k: e[k] for k in ("mode", "object")}
                for p, e in manifest["identity"]["entries"].items()
            }
            require(
                tree(repo, args.ref) == expected,
                "committed tree differs from captured input",
            )
            print(
                "Committed tree matches captured input, including modes and gitlinks."
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
