"""Tests for the bundled unpublished-commit publication checkpoint."""

from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CODEX_SKILL_DIR = PROJECT_ROOT / "assets" / "codex-skills" / "publish-unpushed-commits"
CLAUDE_SKILL_DIR = (
    PROJECT_ROOT / "assets" / "claude-skills" / "publish-unpushed-commits"
)
CODEX_HELPER = CODEX_SKILL_DIR / "scripts" / "publish-checkpoint.py"
CLAUDE_HELPER = CLAUDE_SKILL_DIR / "scripts" / "publish-checkpoint.py"


def _claude_allowed_tools(skill_name: str) -> set[str]:
    """Return one Claude skill's declared tool permissions."""
    content = (
        PROJECT_ROOT / "assets" / "claude-skills" / skill_name / "SKILL.md"
    ).read_text(encoding="utf-8")
    frontmatter = content.split("---\n", 2)[1]
    lines = frontmatter.splitlines()
    start = lines.index("allowed-tools:") + 1
    return {
        line.removeprefix("  - ") for line in lines[start:] if line.startswith("  - ")
    }


def _git(repo: Path, *args: str) -> str:
    """Run Git and return stripped standard output."""
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()


def _helper(
    repo: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run the Codex publication helper."""
    result = subprocess.run(
        [sys.executable, str(CODEX_HELPER), *args],
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
    )
    if check:
        result.check_returncode()
    return result


def _commit(repo: Path, name: str, content: str) -> str:
    """Create one test commit and return its identifier."""
    (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", name)
    _git(repo, "commit", "-q", "-m", f"Add {name}")
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def publication_repo(tmp_path: Path) -> tuple[Path, str, str, str]:
    """Create a repository with a base and two unpublished commits."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "remote", "add", "origin", "https://example.invalid/project.git")
    base = _commit(repo, "base.txt", "base\n")
    bottom = _commit(repo, "bottom.txt", "bottom\n")
    top = _commit(repo, "top.txt", "top\n")
    return repo, base, bottom, top


def _start(
    repo: Path,
    base: str,
    *,
    status: str = "ready",
    provider: str = "github",
    host_url: str = "https://github.com",
    target_repository: str = "upstream/project",
    head_repository: str = "upstream/project",
    target_repository_id: int = 101,
    head_repository_id: int | None = None,
    target_project_id: int = 101,
    head_project_id: int | None = None,
    remote: str = "origin",
    trunk: str = "main",
) -> dict[str, object]:
    """Start one test publication run."""
    arguments = [
        "start",
        "--base",
        base,
        "--target-base",
        base,
        "--provider",
        provider,
        "--host-url",
        host_url,
        "--target-repository",
        target_repository,
        "--head-repository",
        head_repository,
        "--remote",
        remote,
        "--head-push-url",
        _git(repo, "remote", "get-url", "--push", remote),
        "--trunk",
        trunk,
        "--status",
        status,
    ]
    if provider == "gitlab":
        arguments.extend(
            [
                "--target-project-id",
                str(target_project_id),
                "--head-project-id",
                str(
                    head_project_id
                    if head_project_id is not None
                    else target_project_id
                ),
            ]
        )
    else:
        arguments.extend(
            [
                "--target-repository-id",
                str(target_repository_id),
                "--head-repository-id",
                str(
                    head_repository_id
                    if head_repository_id is not None
                    else (
                        target_repository_id
                        if head_repository == target_repository
                        else 202
                    )
                ),
            ]
        )
    result = _helper(repo, *arguments)
    return json.loads(result.stdout)


def _confirm_push(repo: Path, layer: str, head: str) -> None:
    """Record one absent-branch lease and its verified pushed head."""
    prepared = json.loads(
        _helper(
            repo,
            "prepare-push",
            "--layer",
            layer,
            "--expected-old",
            "absent",
        ).stdout
    )
    target = json.loads(_helper(repo, "push-target", "--layer", layer).stdout)
    branch_ref = f"refs/heads/{prepared['branch']}"
    assert target["arguments"] == [
        "-c",
        "push.pushOption=",
        "push",
        "--no-follow-tags",
        "--recurse-submodules=no",
        f"--force-with-lease={branch_ref}:",
        "origin",
        f"{head}:{branch_ref}",
    ]
    assert target["target_repository"]
    if target["provider"] == "github":
        assert target["target_repository_id"] == 101
    else:
        assert target["target_project_id"] == 101
    _helper(repo, "confirm-push", "--layer", layer, "--remote-head", head)


def _record_created_review(
    repo: Path,
    layer: str,
    number: int,
    url: str,
    head: str,
    base: str,
    base_head: str,
    *,
    status: str = "ready",
) -> dict[str, object]:
    """Record one newly created and initially verified review request."""
    result = _helper(
        repo,
        "record-created-review",
        "--layer",
        layer,
        "--number",
        str(number),
        "--url",
        url,
        "--head",
        head,
        "--base",
        base,
        "--base-head",
        base_head,
        "--status",
        status,
    )
    return json.loads(result.stdout)


def _record_stack_plan(
    repo: Path,
    base: str,
    bottom: str,
    top: str,
) -> tuple[Path, dict[str, object]]:
    """Record one two-layer native-stack plan."""
    _git(repo, "branch", "publish/bottom", bottom)
    _git(repo, "branch", "publish/top", top)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "bottom.md").write_text("Bottom body\n", encoding="utf-8")
    (bodies / "top.md").write_text(
        "Top body\n\nDepends on {{PRECEDING_REVIEW_URL}}.\n",
        encoding="utf-8",
    )
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "feature",
                "transport": "github-stack",
                "layers": [
                    {
                        "id": "bottom",
                        "branch": "publish/bottom",
                        "base_branch": "main",
                        "tip": bottom,
                        "title": "Add the bottom behavior",
                        "body_file": "bodies/bottom.md",
                    },
                    {
                        "id": "top",
                        "branch": "publish/top",
                        "base_branch": "publish/bottom",
                        "tip": top,
                        "title": "Complete the top behavior",
                        "body_file": "bodies/top.md",
                    },
                ],
            }
        ],
    }
    plan_path = run_dir / "plan-input.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    recorded = json.loads(_helper(repo, "record-plan", "--file", str(plan_path)).stdout)
    return run_dir, recorded


def test_codex_and_claude_low_freedom_assets_match() -> None:
    """Both assistant variants should share deterministic publication logic."""
    assert CODEX_HELPER.read_bytes() == CLAUDE_HELPER.read_bytes()
    for reference_name in ("github-publication.md", "gitlab-publication.md"):
        assert (CODEX_SKILL_DIR / "references" / reference_name).read_bytes() == (
            CLAUDE_SKILL_DIR / "references" / reference_name
        ).read_bytes()


def test_codex_and_claude_workflow_bodies_match() -> None:
    """Assistant adapters should not drift in publication behavior."""
    codex_content = (CODEX_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    claude_content = (CLAUDE_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    codex_body = codex_content.split("---\n", 2)[2]
    claude_body = claude_content.split("---\n", 2)[2]
    normalized_claude = (
        claude_body.replace("\nInvocation arguments: `$ARGUMENTS`\n\n", "\n")
        .replace(
            "\n/publish-unpushed-commits",
            "\n$publish-unpushed-commits",
        )
        .replace("`/refine-history", "`$refine-history")
        .replace("`/refine-commit-messages", "`$refine-commit-messages")
        .replace(
            '"${CLAUDE_SKILL_DIR}/scripts/publish-checkpoint.py"',
            ".agents/skills/publish-unpushed-commits/scripts/publish-checkpoint.py",
        )
        .replace(".claude/skills/", ".agents/skills/")
    )

    assert normalized_claude == codex_body


def test_claude_publisher_can_execute_refinement_contracts_in_process() -> None:
    """The publisher should permit every tool used by its user-only dependencies."""
    publisher_tools = _claude_allowed_tools("publish-unpushed-commits")
    dependency_tools = _claude_allowed_tools("refine-history") | (
        _claude_allowed_tools("refine-commit-messages")
    )

    assert publisher_tools >= dependency_tools


def test_provider_reference_python_executors_parse() -> None:
    """Every embedded provider executor should remain valid Python."""
    expected_counts = {
        "github-publication.md": 3,
        "gitlab-publication.md": 2,
    }
    pattern = re.compile(r"python3 -c '\n(.*?)\n'(?= )", re.DOTALL)

    for reference_name, expected_count in expected_counts.items():
        content = (CODEX_SKILL_DIR / "references" / reference_name).read_text(
            encoding="utf-8"
        )
        executors = pattern.findall(content)
        assert len(executors) == expected_count
        for position, executor in enumerate(executors, start=1):
            ast.parse(executor, filename=f"{reference_name}:executor-{position}")


def test_skill_contract_defaults_ready_and_never_lands() -> None:
    """The public skill should publish by default without landing work."""
    for skill_dir in (CODEX_SKILL_DIR, CLAUDE_SKILL_DIR):
        content = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert "Treat an invocation without a mode as `ready`" in content
        assert "Draft status is opt-in" in content
        assert "Do not merge" in content
        assert "gh stack merge" not in content
        assert "gh pr merge" not in content
        assert "record-created-review" in content
        assert "git --no-optional-locks ls-remote --tags SOURCE" in content
        assert "peeled target of an annotated" in content
        assert "resolved target and head repositories" in content
        assert "provider-reported clone URL otherwise" in content
        assert "Fetch only the selected publication remote" in content
        assert "Do not infer a different remote from branch configuration" in content
        assert "Never enumerate other configured Git remotes" in content
        assert "every configured remote" not in content
        assert "all configured remotes" not in content
        assert "## Git command concurrency" in content
        assert "## Progress discipline" in content
        assert 'run `python3 "$PUBLISH_HELPER" status --json`' in content
        assert "at the end of every numbered section" in content
        assert (
            "`started` with a normalized result, `planned`, `validated`, and" in content
        )
    reference = (CODEX_SKILL_DIR / "references" / "github-publication.md").read_text(
        encoding="utf-8"
    )
    assert "stack-creation REST endpoint" in reference
    assert "github-request --layer" in reference
    assert '"--method", "POST"' in reference
    assert "head_repo" in reference
    assert "push-target --layer" in reference
    assert 'export GH_HOST="$GITHUB_HOST"' not in reference
    assert 'gh api --hostname "$GITHUB_HOST"' in reference
    assert "owner-qualified head" in reference
    assert "github-stack-request" in reference
    assert "--observations" in reference
    assert 'target["head_repository_id"]' in reference
    assert "gh pr create --head" in reference
    assert "gh pr edit " not in reference
    gitlab_reference = (
        CODEX_SKILL_DIR / "references" / "gitlab-publication.md"
    ).read_text(encoding="utf-8")
    assert "glab api" in gitlab_reference
    assert "glab mr create \\" not in gitlab_reference
    assert '--hostname "$GITLAB_HOST"' in gitlab_reference
    assert "Do not use `glab stack create`" in gitlab_reference
    assert "glab auth status" in gitlab_reference
    assert "optional diagnostics" in gitlab_reference
    assert "api_protocol" in gitlab_reference
    assert "if ! CONFIGURED_API_HOST=$(glab config get api_host" in gitlab_reference
    assert (
        "if ! CONFIGURED_API_PROTOCOL=$(glab config get api_protocol"
        in gitlab_reference
    )
    assert '("head_repository", "head_project_id")' in gitlab_reference
    assert '("target_repository", "target_project_id")' in gitlab_reference
    assert '--number "$MR_NUMBER"' not in gitlab_reference

    claude_skill = (CLAUDE_SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "Invocation arguments: `$ARGUMENTS`" in claude_skill
    assert "${CLAUDE_SKILL_DIR}/scripts/publish-checkpoint.py" in claude_skill
    assert "runtime cannot\nrecursively invoke a user-only skill" in claude_skill
    claude_frontmatter = claude_skill.split("---\n", 2)[1]
    assert "context: fork" not in claude_frontmatter
    assert "background:" not in claude_frontmatter
    assert "Agent(commit-message-drafter)" in claude_skill
    assert "Bash(mktemp *)" in claude_skill
    assert ":*)" not in claude_skill
    assert "Bash(gh pr create *)" not in claude_skill
    assert "Bash(gh pr edit *)" not in claude_skill
    assert "Bash(gh pr merge *)" not in claude_skill
    assert "Bash(gh stack link *)" not in claude_skill
    assert "Bash(glab api *)" in claude_skill


def test_publisher_and_in_process_refiners_avoid_optional_git_locks() -> None:
    """Every documented read-only Git invocation should suppress optional locks."""
    read_only_git = re.compile(
        r"\bgit (?:apply --check|branch|cat-file|diff|diff-tree|for-each-ref|log|"
        r"ls-remote|merge-base|patch-id|range-diff|remote|rev-list|rev-parse|"
        r"show|show-ref|status|symbolic-ref)\b"
    )
    documents = []
    for variant in ("codex-skills", "claude-skills"):
        for skill_name in (
            "publish-unpushed-commits",
            "refine-history",
            "refine-commit-messages",
        ):
            skill_dir = PROJECT_ROOT / "assets" / variant / skill_name
            documents.append(skill_dir / "SKILL.md")
            references_dir = skill_dir / "references"
            if references_dir.is_dir():
                documents.extend(sorted(references_dir.glob("*.md")))

    violations = []
    for document in documents:
        content = document.read_text(encoding="utf-8")
        for match in read_only_git.finditer(content):
            line = content.count("\n", 0, match.start()) + 1
            violations.append(f"{document.relative_to(PROJECT_ROOT)}:{line}")

    assert violations == []


def test_scoped_push_ignores_implicit_tags_and_push_options(tmp_path: Path) -> None:
    """Publication pushes should mutate only their explicit branch refspec."""
    source = tmp_path / "source"
    remote = tmp_path / "remote.git"
    source.mkdir()
    remote.mkdir()
    _git(source, "init", "-q", "-b", "main")
    _git(source, "config", "user.name", "Test User")
    _git(source, "config", "user.email", "test@example.com")
    head = _commit(source, "feature.txt", "feature\n")
    _git(source, "tag", "-a", "release", "-m", "Release")
    _git(remote, "init", "-q", "--bare")
    _git(remote, "config", "receive.advertisePushOptions", "true")
    hook = remote / "hooks" / "pre-receive"
    hook.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "$GIT_PUSH_OPTION_COUNT" > push-option-count\n'
        "cat >/dev/null\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    _git(source, "remote", "add", "origin", str(remote))
    _git(source, "config", "push.followTags", "true")
    _git(source, "config", "push.pushOption", "merge_request.create")

    subprocess.run(
        [
            "git",
            "-c",
            "push.pushOption=",
            "push",
            "--no-follow-tags",
            "--recurse-submodules=no",
            "origin",
            f"{head}:refs/heads/publish/test",
        ],
        cwd=source,
        text=True,
        capture_output=True,
        check=True,
    )

    assert _git(remote, "rev-parse", "refs/heads/publish/test") == head
    tag = subprocess.run(
        ["git", "show-ref", "--verify", "refs/tags/release"],
        cwd=remote,
        text=True,
        capture_output=True,
        check=False,
    )
    assert tag.returncode != 0
    assert (remote / "push-option-count").read_text(encoding="utf-8") == "0\n"


def test_complete_native_stack_publication(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """A complete native stack should reach the published terminal phase."""
    repo, base, bottom, top = publication_repo
    checkpoint = _start(repo, base)
    run_dir, plan = _record_stack_plan(repo, base, bottom, top)

    assert checkpoint["requested_status"] == "ready"
    assert plan["groups"][0]["transport"] == "github-stack"
    assert len(plan["groups"][0]["layers"][0]["body_sha256"]) == 64
    _helper(repo, "advance", "--phase", "validated")
    _helper(repo, "advance", "--phase", "publishing")
    layers = (
        ("bottom", 41, "publish/bottom", "main", base, bottom),
        ("top", 42, "publish/top", "publish/bottom", bottom, top),
    )
    for layer, number, branch, layer_base, base_head, head in layers:
        _confirm_push(repo, layer, head)
        request = json.loads(_helper(repo, "github-request", "--layer", layer).stdout)
        payload = json.loads(Path(request["payload_file"]).read_text(encoding="utf-8"))
        expected_body = (
            "Bottom body\n"
            if layer == "bottom"
            else "Top body\n\nDepends on https://github.com/upstream/project/pull/41.\n"
        )
        expected_body_sha256 = hashlib.sha256(expected_body.encode("utf-8")).hexdigest()
        assert request == {
            "base_branch": layer_base,
            "base_head": base_head,
            "body_file": (
                "bodies/bottom.md"
                if layer == "bottom"
                else f"bodies/rendered/{expected_body_sha256}.md"
            ),
            "body_sha256": expected_body_sha256,
            "endpoint": "repos/upstream/project/pulls",
            "head_branch": branch,
            "head_commit": head,
            "head_repository": "upstream/project",
            "head_repository_id": 101,
            "host_url": "https://github.com",
            "layer": layer,
            "payload_file": str(request["payload_file"]),
            "target_repository": "upstream/project",
            "target_repository_id": 101,
        }
        assert payload == {
            "base": layer_base,
            "body": expected_body,
            "draft": False,
            "head": branch,
            "title": (
                "Add the bottom behavior"
                if layer == "bottom"
                else "Complete the top behavior"
            ),
        }
        _record_created_review(
            repo,
            layer,
            number,
            f"https://github.com/upstream/project/pull/{number}",
            head,
            layer_base,
            base_head,
        )

    unstacked_observations = run_dir / "unstacked.json"
    unstacked_observations.write_text(
        json.dumps(
            {
                "schema": 1,
                "pull_requests": [
                    {"number": 41, "stack": None},
                    {"number": 42, "stack": None},
                ],
            }
        ),
        encoding="utf-8",
    )
    stack_request = json.loads(
        _helper(
            repo,
            "github-stack-request",
            "--group",
            "feature",
            "--observations",
            str(unstacked_observations),
        ).stdout
    )
    assert stack_request["action"] == "create"
    assert stack_request["endpoint"] == "repos/upstream/project/stacks"
    assert [
        pull_request["number"] for pull_request in stack_request["pull_requests"]
    ] == [41, 42]
    assert json.loads(
        Path(stack_request["payload_file"]).read_text(encoding="utf-8")
    ) == {"pull_requests": [41, 42]}
    assert "arguments" not in stack_request
    stacked_observations = run_dir / "stacked.json"
    stacked_observations.write_text(
        json.dumps(
            {
                "schema": 1,
                "pull_requests": [
                    {
                        "number": 41,
                        "stack": {"number": 9, "position": 1, "size": 2},
                    },
                    {
                        "number": 42,
                        "stack": {"number": 9, "position": 2, "size": 2},
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    adopt_target = json.loads(
        _helper(
            repo,
            "github-stack-request",
            "--group",
            "feature",
            "--observations",
            str(stacked_observations),
        ).stdout
    )
    assert adopt_target["action"] == "adopt"
    assert "endpoint" not in adopt_target
    assert "payload_file" not in adopt_target
    assert adopt_target["stack_number"] == 9

    partial_observations = run_dir / "partial-stack.json"
    partial_observations.write_text(
        json.dumps(
            {
                "schema": 1,
                "pull_requests": [
                    {
                        "number": 41,
                        "stack": {"number": 9, "position": 1, "size": 2},
                    },
                    {"number": 42, "stack": None},
                ],
            }
        ),
        encoding="utf-8",
    )
    partial = _helper(
        repo,
        "github-stack-request",
        "--group",
        "feature",
        "--observations",
        str(partial_observations),
        check=False,
    )
    assert partial.returncode != 0
    assert "partial GitHub stack membership" in partial.stderr

    misdirected_stack = _helper(
        repo,
        "github-stack-request",
        "--group",
        "feature",
        "--observations",
        str(unstacked_observations),
        "--url",
        "https://github.com/other/project/pull/99",
        check=False,
    )
    assert misdirected_stack.returncode != 0
    assert "unrecognized arguments" in misdirected_stack.stderr

    for layer, number, branch, layer_base, base_head, head in layers:
        result = _helper(
            repo,
            "record-review",
            "--layer",
            layer,
            "--number",
            str(number),
            "--url",
            f"https://github.com/upstream/project/pull/{number}",
            "--head",
            head,
            "--base",
            layer_base,
            "--base-head",
            base_head,
            "--status",
            "ready",
            "--stack-number",
            "9",
        )
        publication = json.loads(result.stdout)
        assert publication["branch"] == branch

    completed = json.loads(_helper(repo, "finish").stdout)
    assert completed["phase"] == "published"
    assert len(completed["publications"]) == 2
    assert (run_dir / "checkpoint.json").is_file()
    assert _git(repo, "rev-parse", completed["recovery_ref"]) == top

    checkpoint_path = run_dir / "checkpoint.json"
    pristine_checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    for section, message in (
        ("created_reviews", "created review URL"),
        ("publications", "publication URL"),
    ):
        tampered_checkpoint = json.loads(json.dumps(pristine_checkpoint))
        tampered_checkpoint[section]["bottom"]["number"] = 99
        checkpoint_path.write_text(
            json.dumps(tampered_checkpoint) + "\n",
            encoding="utf-8",
        )
        corrupt = _helper(repo, "status", "--json", check=False)
        assert corrupt.returncode != 0
        assert message in corrupt.stderr
    checkpoint_path.write_text(
        json.dumps(pristine_checkpoint) + "\n",
        encoding="utf-8",
    )

    moved_head = _commit(repo, "landing.txt", "landing\n")
    _git(repo, "branch", "-f", "publish/top", moved_head)
    status = json.loads(_helper(repo, "status", "--json").stdout)
    assert status["phase"] == "published"


def test_github_request_marks_only_explicit_drafts(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """GitHub creation arguments should make draft publication opt-in."""
    repo, base, _bottom, top = publication_repo
    _start(repo, base, status="draft")
    _git(repo, "branch", "publish/draft", top)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "draft.md").write_text("Draft body\n", encoding="utf-8")
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "draft",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "draft",
                        "branch": "publish/draft",
                        "base_branch": "main",
                        "tip": top,
                        "title": "Publish the draft",
                        "body_file": "bodies/draft.md",
                    }
                ],
            }
        ],
    }
    plan_path = run_dir / "draft-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    _helper(repo, "record-plan", "--file", str(plan_path))
    _helper(repo, "advance", "--phase", "validated")
    _helper(repo, "advance", "--phase", "publishing")
    _confirm_push(repo, "draft", top)

    request = json.loads(_helper(repo, "github-request", "--layer", "draft").stdout)
    payload = json.loads(Path(request["payload_file"]).read_text(encoding="utf-8"))

    assert payload["draft"] is True


def test_native_stack_number_cannot_cross_publication_groups(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """One provider stack identity must not be reused by an independent group."""
    repo, base, bottom, top = publication_repo
    third = _commit(repo, "third.txt", "third\n")
    integration_tip = _commit(repo, "fourth.txt", "fourth\n")
    _start(repo, base)
    _git(repo, "branch", "publish/first-bottom", bottom)
    _git(repo, "branch", "publish/first-top", top)
    _git(repo, "switch", "-q", "-c", "publish/second-bottom", base)
    _git(repo, "cherry-pick", third)
    second_bottom = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "publish/second-top")
    _git(repo, "cherry-pick", integration_tip)
    second_top = _git(repo, "rev-parse", "HEAD")
    _helper(repo, "record-normalized", "--tip", integration_tip)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    for layer_id in ("first-bottom", "first-top", "second-bottom", "second-top"):
        (bodies / f"{layer_id}.md").write_text(
            f"Body for {layer_id}\n"
            + (
                "\nDepends on {{PRECEDING_REVIEW_URL}}.\n"
                if layer_id.endswith("-top")
                else ""
            ),
            encoding="utf-8",
        )
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": integration_tip,
        "groups": [
            {
                "id": "first",
                "transport": "github-stack",
                "layers": [
                    {
                        "id": "first-bottom",
                        "branch": "publish/first-bottom",
                        "base_branch": "main",
                        "tip": bottom,
                        "title": "Publish the first prerequisite",
                        "body_file": "bodies/first-bottom.md",
                    },
                    {
                        "id": "first-top",
                        "branch": "publish/first-top",
                        "base_branch": "publish/first-bottom",
                        "tip": top,
                        "title": "Publish the first dependent change",
                        "body_file": "bodies/first-top.md",
                    },
                ],
            },
            {
                "id": "second",
                "transport": "github-stack",
                "layers": [
                    {
                        "id": "second-bottom",
                        "branch": "publish/second-bottom",
                        "base_branch": "main",
                        "tip": second_bottom,
                        "title": "Publish the second prerequisite",
                        "body_file": "bodies/second-bottom.md",
                    },
                    {
                        "id": "second-top",
                        "branch": "publish/second-top",
                        "base_branch": "publish/second-bottom",
                        "tip": second_top,
                        "title": "Publish the second dependent change",
                        "body_file": "bodies/second-top.md",
                    },
                ],
            },
        ],
    }
    plan_path = run_dir / "two-stacks.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    _helper(repo, "record-plan", "--file", str(plan_path))
    _helper(repo, "advance", "--phase", "validated")
    _helper(repo, "advance", "--phase", "publishing")

    layers = (
        ("first-bottom", 41, bottom, "main", base),
        ("first-top", 42, top, "publish/first-bottom", bottom),
        ("second-bottom", 51, second_bottom, "main", base),
        (
            "second-top",
            52,
            second_top,
            "publish/second-bottom",
            second_bottom,
        ),
    )
    for layer, number, head, base_branch, base_head in layers:
        _confirm_push(repo, layer, head)
        _record_created_review(
            repo,
            layer,
            number,
            f"https://github.com/upstream/project/pull/{number}",
            head,
            base_branch,
            base_head,
        )

    for layer, number, head, base_branch, base_head in layers[:2]:
        _helper(
            repo,
            "record-review",
            "--layer",
            layer,
            "--number",
            str(number),
            "--url",
            f"https://github.com/upstream/project/pull/{number}",
            "--head",
            head,
            "--base",
            base_branch,
            "--base-head",
            base_head,
            "--status",
            "ready",
            "--stack-number",
            "9",
        )

    layer, number, head, base_branch, base_head = layers[2]
    reused = _helper(
        repo,
        "record-review",
        "--layer",
        layer,
        "--number",
        str(number),
        "--url",
        f"https://github.com/upstream/project/pull/{number}",
        "--head",
        head,
        "--base",
        base_branch,
        "--base-head",
        base_head,
        "--status",
        "ready",
        "--stack-number",
        "9",
        check=False,
    )

    assert reused.returncode != 0
    assert "already assigned to another publication group" in reused.stderr
    status = json.loads(_helper(repo, "status", "--json").stdout)
    assert "second-bottom" not in status["publications"]

    for layer, number, head, base_branch, base_head in layers[2:]:
        _helper(
            repo,
            "record-review",
            "--layer",
            layer,
            "--number",
            str(number),
            "--url",
            f"https://github.com/upstream/project/pull/{number}",
            "--head",
            head,
            "--base",
            base_branch,
            "--base-head",
            base_head,
            "--status",
            "ready",
            "--stack-number",
            "10",
        )

    completed = json.loads(_helper(repo, "finish").stdout)
    assert completed["phase"] == "published"


def test_complete_gitlab_stack_publication(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """GitLab should accept nested projects and a verified branch-chain stack."""
    repo, base, bottom, top = publication_repo
    checkpoint = _start(
        repo,
        base,
        provider="gitlab",
        host_url="https://gitlab.example.com",
        target_repository="group/subgroup/project",
        head_repository="group/subgroup/project",
    )
    _git(repo, "branch", "publish/bottom", bottom)
    _git(repo, "branch", "publish/top", top)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "bottom.md").write_text("Bottom body\n", encoding="utf-8")
    (bodies / "top.md").write_text(
        "Top body\n\nDepends on {{PRECEDING_REVIEW_URL}}.\n",
        encoding="utf-8",
    )
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "gitlab-feature",
                "transport": "gitlab-stack",
                "layers": [
                    {
                        "id": "gitlab-bottom",
                        "branch": "publish/bottom",
                        "base_branch": "main",
                        "tip": bottom,
                        "title": "Add the GitLab prerequisite",
                        "body_file": "bodies/bottom.md",
                    },
                    {
                        "id": "gitlab-top",
                        "branch": "publish/top",
                        "base_branch": "publish/bottom",
                        "tip": top,
                        "title": "Add the GitLab dependent change",
                        "body_file": "bodies/top.md",
                    },
                ],
            }
        ],
    }
    plan_input = run_dir / "gitlab-plan.json"
    plan_input.write_text(json.dumps(plan), encoding="utf-8")
    _helper(repo, "record-plan", "--file", str(plan_input))
    _helper(repo, "advance", "--phase", "validated")
    _helper(repo, "advance", "--phase", "publishing")

    for layer, number, layer_base, base_head, head in (
        ("gitlab-bottom", 51, "main", base, bottom),
        ("gitlab-top", 52, "publish/bottom", bottom, top),
    ):
        _confirm_push(repo, layer, head)
        _record_created_review(
            repo,
            layer,
            number,
            "https://gitlab.example.com/group/subgroup/project/"
            f"-/merge_requests/{number}",
            head,
            layer_base,
            base_head,
        )
        _helper(
            repo,
            "record-review",
            "--layer",
            layer,
            "--number",
            str(number),
            "--url",
            "https://gitlab.example.com/group/subgroup/project/"
            f"-/merge_requests/{number}",
            "--head",
            head,
            "--base",
            layer_base,
            "--base-head",
            base_head,
            "--status",
            "ready",
        )

    completed = json.loads(_helper(repo, "finish").stdout)
    assert checkpoint["provider"] == "gitlab"
    assert checkpoint["host_url"] == "https://gitlab.example.com"
    assert checkpoint["target_project_id"] == 101
    assert checkpoint["head_project_id"] == 101
    assert completed["phase"] == "published"
    assert all(
        publication["stack_number"] is None
        for publication in completed["publications"].values()
    )


def test_gitlab_cross_fork_request_uses_pinned_projects_and_exact_json(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """GitLab fork publication should not depend on mutable local remotes."""
    repo, base, _bottom, top = publication_repo
    _start(
        repo,
        base,
        provider="gitlab",
        host_url="https://gitlab.example.com:8443",
        target_repository="upstream/team/project",
        head_repository="contributor/forks/project",
        target_project_id=101,
        head_project_id=202,
    )
    _git(repo, "branch", "publish/fork", top)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    body = "Exact description with a terminal newline.\n"
    (bodies / "fork.md").write_text(body, encoding="utf-8")
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "fork-series",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "fork-layer",
                        "branch": "publish/fork",
                        "base_branch": "main",
                        "tip": top,
                        "title": "Publish through the fork",
                        "body_file": "bodies/fork.md",
                    }
                ],
            }
        ],
    }
    plan_path = run_dir / "fork-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    (bodies / "fork.md").write_text("/label security\n", encoding="utf-8")
    unsafe = _helper(repo, "record-plan", "--file", str(plan_path), check=False)
    assert unsafe.returncode != 0
    assert "possible quick action" in unsafe.stderr
    (bodies / "fork.md").write_text(body, encoding="utf-8")
    _helper(repo, "record-plan", "--file", str(plan_path))
    _helper(repo, "advance", "--phase", "validated")
    _helper(repo, "advance", "--phase", "publishing")
    _confirm_push(repo, "fork-layer", top)

    request = json.loads(
        _helper(repo, "gitlab-request", "--layer", "fork-layer").stdout
    )
    payload = json.loads(Path(request["payload_file"]).read_text(encoding="utf-8"))
    body_sha256 = hashlib.sha256(body.encode("utf-8")).hexdigest()

    assert request == {
        "base_branch": "main",
        "base_head": base,
        "body_file": "bodies/fork.md",
        "body_sha256": body_sha256,
        "endpoint": "projects/202/merge_requests",
        "head_branch": "publish/fork",
        "head_commit": top,
        "head_project_id": 202,
        "head_repository": "contributor/forks/project",
        "host_url": "https://gitlab.example.com:8443",
        "payload_file": str(request["payload_file"]),
        "target_project_id": 101,
        "target_repository": "upstream/team/project",
    }
    assert payload == {
        "source_branch": "publish/fork",
        "target_branch": "main",
        "title": "Publish through the fork",
        "description": body,
        "target_project_id": 101,
    }
    created = _record_created_review(
        repo,
        "fork-layer",
        77,
        "https://gitlab.example.com:8443/upstream/team/project/-/merge_requests/77",
        top,
        "main",
        base,
    )
    assert created["number"] == 77

    duplicate_request = _helper(
        repo,
        "gitlab-request",
        "--layer",
        "fork-layer",
        check=False,
    )
    assert duplicate_request.returncode != 0
    assert "already recorded as created" in duplicate_request.stderr

    wrong_final_identity = _helper(
        repo,
        "record-review",
        "--layer",
        "fork-layer",
        "--number",
        "78",
        "--url",
        "https://gitlab.example.com:8443/upstream/team/project/-/merge_requests/78",
        "--head",
        top,
        "--base",
        "main",
        "--base-head",
        base,
        "--status",
        "ready",
        check=False,
    )
    assert wrong_final_identity.returncode != 0
    assert "final review identity changed" in wrong_final_identity.stderr


def test_gitlab_draft_title_is_rendered_exactly_once(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Direct API payloads should preserve the planned GitLab draft prefix."""
    repo, base, _bottom, top = publication_repo
    _start(
        repo,
        base,
        status="draft",
        provider="gitlab",
        host_url="https://gitlab.example.com",
    )
    _git(repo, "branch", "publish/draft", top)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "draft.md").write_text("Draft description\n", encoding="utf-8")
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "draft",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "draft",
                        "branch": "publish/draft",
                        "base_branch": "main",
                        "tip": top,
                        "title": "Publish the draft",
                        "body_file": "bodies/draft.md",
                    }
                ],
            }
        ],
    }
    plan_path = run_dir / "draft-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    missing_prefix = _helper(repo, "record-plan", "--file", str(plan_path), check=False)
    assert missing_prefix.returncode != 0
    assert "must begin with 'Draft: '" in missing_prefix.stderr

    plan["groups"][0]["layers"][0]["title"] = "Draft: Publish the draft"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    _helper(repo, "record-plan", "--file", str(plan_path))
    _helper(repo, "advance", "--phase", "validated")
    _helper(repo, "advance", "--phase", "publishing")
    _confirm_push(repo, "draft", top)
    request = json.loads(_helper(repo, "gitlab-request", "--layer", "draft").stdout)
    payload = json.loads(Path(request["payload_file"]).read_text(encoding="utf-8"))

    assert payload["title"] == "Draft: Publish the draft"
    assert payload["title"].count("Draft: ") == 1


def test_gitlab_rejects_relative_url_hosts_before_recovery_state(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Pathful self-managed hosts are ambiguous for glab host selection."""
    repo, base, _bottom, _top = publication_repo

    result = _helper(
        repo,
        "start",
        "--base",
        base,
        "--target-base",
        base,
        "--provider",
        "gitlab",
        "--host-url",
        "https://example.com/gitlab",
        "--target-repository",
        "group/project",
        "--head-repository",
        "group/project",
        "--target-project-id",
        "101",
        "--head-project-id",
        "101",
        "--remote",
        "origin",
        "--trunk",
        "main",
        "--status",
        "ready",
        check=False,
    )

    assert result.returncode != 0
    assert "host_url" in result.stderr
    state_dir = Path(_helper(repo, "state-dir").stdout.strip())
    assert not state_dir.exists()


def test_unfinished_run_blocks_replacement(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Starting again must not erase resumable publication state."""
    repo, base, _bottom, _top = publication_repo
    _start(repo, base)

    result = _helper(
        repo,
        "start",
        "--base",
        base,
        "--target-base",
        base,
        "--provider",
        "github",
        "--host-url",
        "https://github.com",
        "--target-repository",
        "upstream/project",
        "--head-repository",
        "upstream/project",
        "--target-repository-id",
        "101",
        "--head-repository-id",
        "101",
        "--remote",
        "origin",
        "--head-push-url",
        "https://example.invalid/project.git",
        "--trunk",
        "main",
        "--status",
        "draft",
        check=False,
    )

    assert result.returncode != 0
    assert "unfinished publication run" in result.stderr
    status = json.loads(_helper(repo, "status", "--json").stdout)
    assert status["requested_status"] == "ready"


def test_corrupt_checkpoint_fails_closed(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Resume should reject malformed recovery state."""
    repo, base, _bottom, _top = publication_repo
    _start(repo, base)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    (run_dir / "checkpoint.json").write_text("[]\n", encoding="utf-8")

    result = _helper(repo, "status", "--json", check=False)

    assert result.returncode != 0
    assert "expected a JSON object" in result.stderr


def test_record_normalized_rejects_a_net_zero_range(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """A nonempty commit range must still have a publishable tree outcome."""
    repo, base, _bottom, _top = publication_repo
    _git(repo, "switch", "-q", "-c", "publication/net-zero", base)
    _commit(repo, "temporary.txt", "temporary\n")
    _git(repo, "rm", "-q", "temporary.txt")
    _git(repo, "commit", "-q", "-m", "Remove temporary file")
    _start(repo, base)

    result = _helper(repo, "record-normalized", "--tip", "HEAD", check=False)

    assert result.returncode != 0
    assert "no aggregate tree change" in result.stderr


def test_refresh_normalized_rejects_a_net_zero_range(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """A trunk refresh cannot replace the plan with an empty outcome."""
    repo, base, _bottom, top = publication_repo
    _start(repo, base)
    _helper(repo, "record-normalized", "--tip", top)
    _git(repo, "switch", "-q", "-c", "publication/refreshed-base", base)
    target_base = _commit(repo, "trunk.txt", "advanced trunk\n")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "Empty publication")
    empty_tip = _git(repo, "rev-parse", "HEAD")

    result = _helper(
        repo,
        "refresh-normalized",
        "--target-base",
        target_base,
        "--tip",
        empty_tip,
        check=False,
    )

    assert result.returncode != 0
    assert "no aggregate tree change" in result.stderr


def test_plan_rejects_an_empty_incremental_layer(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """An empty commit cannot become a review layer with no tree change."""
    repo, base, bottom, top = publication_repo
    _start(repo, base)
    _git(repo, "switch", "-q", "-c", "publish/empty", base)
    _git(repo, "commit", "-q", "--allow-empty", "-m", "Empty groundwork")
    empty_tip = _git(repo, "rev-parse", "HEAD")
    _git(repo, "switch", "-q", "-c", "publish/final")
    _git(repo, "cherry-pick", bottom, top)
    final_tip = _git(repo, "rev-parse", "HEAD")
    _helper(repo, "record-normalized", "--tip", final_tip)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "empty.md").write_text("Empty groundwork\n", encoding="utf-8")
    (bodies / "final.md").write_text(
        "Final outcome\n\nDepends on {{PRECEDING_REVIEW_URL}}.\n",
        encoding="utf-8",
    )
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": final_tip,
        "groups": [
            {
                "id": "feature",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "empty",
                        "branch": "publish/empty",
                        "base_branch": "main",
                        "tip": empty_tip,
                        "title": "Publish empty groundwork",
                        "body_file": "bodies/empty.md",
                    },
                    {
                        "id": "final",
                        "branch": "publish/final",
                        "base_branch": "main",
                        "tip": final_tip,
                        "title": "Publish the outcome",
                        "body_file": "bodies/final.md",
                    },
                ],
            }
        ],
    }
    plan_path = run_dir / "empty-layer-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    result = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert result.returncode != 0
    assert "layer empty has no incremental tree change" in result.stderr


def test_plan_rejects_a_net_zero_group(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Add-then-remove layers cannot form an empty review-request group."""
    repo, base, _bottom, top = publication_repo
    _start(repo, base)
    _git(repo, "switch", "-q", "-c", "publish/temporary-add", base)
    temporary_add = _commit(repo, "temporary.txt", "temporary\n")
    _git(repo, "switch", "-q", "-c", "publish/temporary-remove")
    _git(repo, "rm", "-q", "temporary.txt")
    _git(repo, "commit", "-q", "-m", "Remove temporary file")
    temporary_remove = _git(repo, "rev-parse", "HEAD")
    _git(repo, "branch", "publish/feature", top)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "temporary-add.md").write_text("Add temporary file\n", encoding="utf-8")
    (bodies / "temporary-remove.md").write_text(
        "Remove it\n\nDepends on {{PRECEDING_REVIEW_URL}}.\n",
        encoding="utf-8",
    )
    (bodies / "feature.md").write_text("Publish feature\n", encoding="utf-8")
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "temporary",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "temporary-add",
                        "branch": "publish/temporary-add",
                        "base_branch": "main",
                        "tip": temporary_add,
                        "title": "Add a temporary file",
                        "body_file": "bodies/temporary-add.md",
                    },
                    {
                        "id": "temporary-remove",
                        "branch": "publish/temporary-remove",
                        "base_branch": "main",
                        "tip": temporary_remove,
                        "title": "Remove the temporary file",
                        "body_file": "bodies/temporary-remove.md",
                    },
                ],
            },
            {
                "id": "feature",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "feature",
                        "branch": "publish/feature",
                        "base_branch": "main",
                        "tip": top,
                        "title": "Publish the feature",
                        "body_file": "bodies/feature.md",
                    }
                ],
            },
        ],
    }
    plan_path = run_dir / "net-zero-group-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    result = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert result.returncode != 0
    assert "group temporary has no aggregate tree change" in result.stderr


def test_plan_rejects_a_github_stack_above_one_hundred_layers(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """GitHub's stack bound must fail before any layer can be published."""
    repo, base, _bottom, top = publication_repo
    _start(repo, base)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "oversized",
                "transport": "github-stack",
                "layers": [{} for _position in range(101)],
            }
        ],
    }
    plan_path = run_dir / "oversized-stack-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    result = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert result.returncode != 0
    assert "more than one hundred layers" in result.stderr


def test_plan_rejects_a_gitlab_stack_above_ten_layers(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """GitLab's stack bound must fail before any layer can be published."""
    repo, base, _bottom, top = publication_repo
    _start(repo, base, provider="gitlab", host_url="https://gitlab.example.com")
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "oversized",
                "transport": "gitlab-stack",
                "layers": [{} for _position in range(11)],
            }
        ],
    }
    plan_path = run_dir / "oversized-stack-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    result = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert result.returncode != 0
    assert "more than ten layers" in result.stderr


def test_ordinary_same_project_series_requires_trunk_bases(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """An ordinary GitLab series must not accidentally form a native stack."""
    repo, base, bottom, top = publication_repo
    _start(repo, base, provider="gitlab", host_url="https://gitlab.example.com")
    _git(repo, "branch", "publish/bottom", bottom)
    _git(repo, "branch", "publish/top", top)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "bottom.md").write_text("Bottom\n", encoding="utf-8")
    (bodies / "top.md").write_text(
        "Top\n\nDepends on {{PRECEDING_REVIEW_URL}}.\n",
        encoding="utf-8",
    )
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "ordinary-series",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "bottom",
                        "branch": "publish/bottom",
                        "base_branch": "main",
                        "tip": bottom,
                        "title": "Publish the prerequisite",
                        "body_file": "bodies/bottom.md",
                    },
                    {
                        "id": "top",
                        "branch": "publish/top",
                        "base_branch": "publish/bottom",
                        "tip": top,
                        "title": "Publish the dependent change",
                        "body_file": "bodies/top.md",
                    },
                ],
            }
        ],
    }
    plan_path = run_dir / "ordinary-series-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    chained_result = _helper(
        repo,
        "record-plan",
        "--file",
        str(plan_path),
        check=False,
    )
    assert chained_result.returncode != 0
    assert "publication layer top must target main" in chained_result.stderr

    plan["groups"][0]["layers"][1]["base_branch"] = "main"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    _helper(repo, "record-plan", "--file", str(plan_path))


def test_plan_requires_exact_preceding_review_placeholders(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Only higher layers may contain one predecessor URL placeholder."""
    repo, base, bottom, top = publication_repo
    _start(repo, base)
    run_dir, plan = _record_stack_plan(repo, base, bottom, top)
    plan_path = run_dir / "placeholder-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    bottom_body = run_dir / "bodies" / "bottom.md"
    top_body = run_dir / "bodies" / "top.md"

    bottom_body.write_text("{{PRECEDING_REVIEW_URL}}\n", encoding="utf-8")
    bottom_result = _helper(
        repo,
        "record-plan",
        "--file",
        str(plan_path),
        check=False,
    )
    assert bottom_result.returncode != 0
    assert "bottom publication layer cannot name a preceding review" in (
        bottom_result.stderr
    )

    bottom_body.write_text("Bottom body\n", encoding="utf-8")
    top_body.write_text("Top body without a link\n", encoding="utf-8")
    missing_result = _helper(
        repo,
        "record-plan",
        "--file",
        str(plan_path),
        check=False,
    )
    assert missing_result.returncode != 0
    assert "must contain exactly one" in missing_result.stderr

    top_body.write_text(
        "{{PRECEDING_REVIEW_URL}} and {{PRECEDING_REVIEW_URL}}\n",
        encoding="utf-8",
    )
    duplicate_result = _helper(
        repo,
        "record-plan",
        "--file",
        str(plan_path),
        check=False,
    )
    assert duplicate_result.returncode != 0
    assert "must contain exactly one" in duplicate_result.stderr


def test_plan_rejects_missing_body_file(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """A plan must freeze every exact pull request body before publication."""
    repo, base, bottom, top = publication_repo
    _start(repo, base)
    _git(repo, "branch", "publish/bottom", bottom)
    _git(repo, "branch", "publish/top", top)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "feature",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "feature",
                        "branch": "publish/top",
                        "base_branch": "main",
                        "tip": top,
                        "title": "Publish the feature",
                        "body_file": "bodies/missing.md",
                    }
                ],
            }
        ],
    }
    plan_path = run_dir / "plan-input.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    result = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert result.returncode != 0
    assert "required publication body file is missing" in result.stderr


def test_plan_rejects_blank_body_file(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Whitespace cannot satisfy the exact review-body contract."""
    repo, base, _bottom, top = publication_repo
    _start(repo, base)
    _git(repo, "branch", "publish/top", top)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "blank.md").write_text(" \n\t\n", encoding="utf-8")
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "feature",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "feature",
                        "branch": "publish/top",
                        "base_branch": "main",
                        "tip": top,
                        "title": "Publish the feature",
                        "body_file": "bodies/blank.md",
                    }
                ],
            }
        ],
    }
    plan_path = run_dir / "plan-input.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    result = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert result.returncode != 0
    assert "body file is empty or blank" in result.stderr


def test_plan_rejects_the_target_trunk_as_a_publication_branch(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """A publication plan must never turn its target trunk into a push target."""
    repo, base, _bottom, top = publication_repo
    _start(repo, base)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "unsafe.md").write_text("Unsafe trunk plan\n", encoding="utf-8")
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "unsafe",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "unsafe",
                        "branch": "main",
                        "base_branch": "main",
                        "tip": top,
                        "title": "Publish the unsafe plan",
                        "body_file": "bodies/unsafe.md",
                    }
                ],
            }
        ],
    }
    plan_path = run_dir / "unsafe-plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    result = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert result.returncode != 0
    assert "cannot use the target trunk branch" in result.stderr


def test_plan_rejects_omitted_and_extra_aggregate_changes(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Every group outcome should compose to exactly the normalized tree."""
    repo, base, bottom, top = publication_repo
    _start(repo, base)
    _git(repo, "branch", "publish/incomplete", bottom)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "outcome.md").write_text("Outcome\n", encoding="utf-8")
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "outcome",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "outcome",
                        "branch": "publish/incomplete",
                        "base_branch": "main",
                        "tip": bottom,
                        "title": "Publish the outcome",
                        "body_file": "bodies/outcome.md",
                    }
                ],
            }
        ],
    }
    plan_path = run_dir / "plan-input.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    omitted = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert omitted.returncode != 0
    assert "do not compose the normalized aggregate tree" in omitted.stderr

    extra = _commit(repo, "extra.txt", "extra\n")
    _git(repo, "branch", "publish/extra", extra)
    plan["groups"][0]["layers"][0]["branch"] = "publish/extra"
    plan["groups"][0]["layers"][0]["tip"] = extra
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    unexpected = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert unexpected.returncode != 0
    assert "do not compose the normalized aggregate tree" in unexpected.stderr


def test_plan_accepts_independent_groups_that_compose_the_aggregate(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Independent group diffs should be composed from their shared base."""
    repo, base, bottom, top = publication_repo
    _start(repo, base)
    _git(repo, "branch", "publish/first", bottom)
    _git(repo, "switch", "-q", "-c", "publish/second", base)
    _git(repo, "cherry-pick", top)
    second = _git(repo, "rev-parse", "HEAD")
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "first.md").write_text("First outcome\n", encoding="utf-8")
    (bodies / "second.md").write_text("Second outcome\n", encoding="utf-8")
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "first",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "first",
                        "branch": "publish/first",
                        "base_branch": "main",
                        "tip": bottom,
                        "title": "Publish the first outcome",
                        "body_file": "bodies/first.md",
                    }
                ],
            },
            {
                "id": "second",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "second",
                        "branch": "publish/second",
                        "base_branch": "main",
                        "tip": second,
                        "title": "Publish the second outcome",
                        "body_file": "bodies/second.md",
                    }
                ],
            },
        ],
    }
    plan_path = run_dir / "plan-input.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    recorded = json.loads(_helper(repo, "record-plan", "--file", str(plan_path)).stdout)

    assert [group["id"] for group in recorded["groups"]] == ["first", "second"]


def test_prepared_push_can_resume_after_remote_side_effect(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """A crash after pushing should retain the exact old and intended objects."""
    repo, base, bottom, top = publication_repo
    _git(
        repo,
        "remote",
        "set-url",
        "--push",
        "origin",
        "https://oauth2:old-token@example.invalid/project.git?access_token=old#old",
    )
    _start(repo, base)
    _run_dir, _plan = _record_stack_plan(repo, base, bottom, top)
    _helper(repo, "advance", "--phase", "validated")
    _helper(repo, "advance", "--phase", "publishing")

    prepared = json.loads(
        _helper(
            repo,
            "prepare-push",
            "--layer",
            "bottom",
            "--expected-old",
            "absent",
        ).stdout
    )
    resumed = json.loads(_helper(repo, "status", "--json").stdout)

    assert prepared["expected_old"] is None
    assert prepared["planned_head"] == bottom
    assert prepared["confirmed_head"] is None
    assert resumed["remote_branches"]["bottom"] == prepared

    _git(
        repo,
        "remote",
        "set-url",
        "--push",
        "origin",
        "https://oauth2:new-token@example.invalid/project.git?access_token=new#new",
    )
    rotated = json.loads(_helper(repo, "status", "--json").stdout)
    assert rotated["push_url_sha256"] == resumed["push_url_sha256"]

    _git(repo, "remote", "set-url", "--push", "origin", "https://wrong.invalid/x.git")
    remapped = _helper(repo, "status", "--json", check=False)
    assert remapped.returncode != 0
    assert "push remote URL changed" in remapped.stderr
    _git(
        repo,
        "remote",
        "set-url",
        "--push",
        "origin",
        "https://oauth2:new-token@example.invalid/project.git?access_token=new#new",
    )

    premature = _helper(
        repo,
        "record-review",
        "--layer",
        "bottom",
        "--number",
        "41",
        "--url",
        "https://github.com/upstream/project/pull/41",
        "--head",
        bottom,
        "--base",
        "main",
        "--base-head",
        base,
        "--status",
        "ready",
        "--stack-number",
        "9",
        check=False,
    )
    assert premature.returncode != 0
    assert "confirmed remote branch" in premature.stderr

    confirmed = json.loads(
        _helper(
            repo,
            "confirm-push",
            "--layer",
            "bottom",
            "--remote-head",
            bottom,
        ).stdout
    )
    assert confirmed["confirmed_head"] == bottom


def test_fresh_push_lease_requires_an_absent_remote_branch(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """A first preparation must never adopt and overwrite an existing branch."""
    repo, base, bottom, top = publication_repo
    _start(repo, base)
    _run_dir, _plan = _record_stack_plan(repo, base, bottom, top)
    _helper(repo, "advance", "--phase", "validated")
    _helper(repo, "advance", "--phase", "publishing")

    unsafe = _helper(
        repo,
        "prepare-push",
        "--layer",
        "bottom",
        "--expected-old",
        base,
        check=False,
    )

    assert unsafe.returncode != 0
    assert "fresh publication branch must be absent" in unsafe.stderr
    prepared = json.loads(
        _helper(
            repo,
            "prepare-push",
            "--layer",
            "bottom",
            "--expected-old",
            "absent",
        ).stdout
    )
    resumed = json.loads(
        _helper(
            repo,
            "prepare-push",
            "--layer",
            "bottom",
            "--expected-old",
            "absent",
        ).stdout
    )
    assert resumed == prepared


def test_resume_rejects_noncredential_http_query_remap(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Only explicitly recognized credential parameters may rotate in a URL."""
    repo, base, _bottom, _top = publication_repo
    original = (
        "https://oauth2:old@example.invalid/project.git?"
        "access_token=old&repository_route=first"
    )
    _git(repo, "remote", "set-url", "--push", "origin", original)
    _start(repo, base)

    _git(
        repo,
        "remote",
        "set-url",
        "--push",
        "origin",
        "https://oauth2:new@example.invalid/project.git?"
        "access_token=new&repository_route=second",
    )
    remapped = _helper(repo, "status", "--json", check=False)

    assert remapped.returncode != 0
    assert "push remote URL changed" in remapped.stderr


@pytest.mark.parametrize(
    ("original_url", "remapped_url"),
    (
        (
            "alice@example.invalid:team/project.git",
            "bob@example.invalid:team/project.git",
        ),
        (
            "ssh://alice@example.invalid/team/project.git",
            "ssh://bob@example.invalid/team/project.git",
        ),
        (
            "alice@example.invalid:/srv/team/project.git",
            "alice@example.invalid:srv/team/project.git",
        ),
        (
            "ssh://alice@example.invalid//srv/team/project.git",
            "ssh://alice@example.invalid/srv/team/project.git",
        ),
    ),
)
def test_resume_rejects_ssh_destination_remap(
    publication_repo: tuple[Path, str, str, str],
    original_url: str,
    remapped_url: str,
) -> None:
    """SSH identity must preserve accounts and absolute-path routing."""
    repo, base, _bottom, _top = publication_repo
    _git(repo, "remote", "set-url", "--push", "origin", original_url)
    _start(repo, base)

    _git(repo, "remote", "set-url", "--push", "origin", remapped_url)
    remapped = _helper(repo, "status", "--json", check=False)

    assert remapped.returncode != 0
    assert "push remote URL changed" in remapped.stderr


def test_target_advance_can_replace_the_plan_before_validation(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """A moving trunk should have an explicit resumable preflight transition."""
    repo, base, bottom, top = publication_repo
    _start(repo, base)
    run_dir, _plan = _record_stack_plan(repo, base, bottom, top)

    _git(repo, "switch", "-q", "-c", "updated-trunk", base)
    target_base = _commit(repo, "trunk.txt", "advanced\n")
    _git(repo, "switch", "-q", "-c", "publish/refreshed")
    _git(repo, "cherry-pick", bottom, top)
    refreshed_tip = _git(repo, "rev-parse", "HEAD")

    refreshed = json.loads(
        _helper(
            repo,
            "refresh-normalized",
            "--target-base",
            target_base,
            "--tip",
            refreshed_tip,
        ).stdout
    )

    assert refreshed["phase"] == "started"
    assert refreshed["target_base"] == target_base
    assert refreshed["normalized_tip"] == refreshed_tip

    rewind = _helper(
        repo,
        "refresh-normalized",
        "--target-base",
        base,
        "--tip",
        top,
        check=False,
    )
    assert rewind.returncode != 0
    assert "did not advance by fast-forward" in rewind.stderr

    _git(repo, "branch", "publish/refreshed-bottom", f"{refreshed_tip}^")
    (run_dir / "bodies" / "refreshed-bottom.md").write_text(
        "Refreshed bottom body\n",
        encoding="utf-8",
    )
    (run_dir / "bodies" / "refreshed-top.md").write_text(
        "Refreshed top body\n\nDepends on {{PRECEDING_REVIEW_URL}}.\n",
        encoding="utf-8",
    )
    replacement_plan = {
        "schema": 1,
        "base": target_base,
        "integration_tip": refreshed_tip,
        "groups": [
            {
                "id": "refreshed-feature",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "refreshed-bottom",
                        "branch": "publish/refreshed-bottom",
                        "base_branch": "main",
                        "tip": _git(repo, "rev-parse", f"{refreshed_tip}^"),
                        "title": "Publish the refreshed prerequisite",
                        "body_file": "bodies/refreshed-bottom.md",
                    },
                    {
                        "id": "refreshed-top",
                        "branch": "publish/refreshed",
                        "base_branch": "main",
                        "tip": refreshed_tip,
                        "title": "Publish the refreshed dependent change",
                        "body_file": "bodies/refreshed-top.md",
                    },
                ],
            }
        ],
    }
    replacement_path = run_dir / "replacement-plan.json"
    replacement_path.write_text(json.dumps(replacement_plan), encoding="utf-8")

    recorded = json.loads(
        _helper(repo, "record-plan", "--file", str(replacement_path)).stdout
    )

    assert recorded["base"] == target_base
    assert recorded["integration_tip"] == refreshed_tip
    assert json.loads(_helper(repo, "status", "--json").stdout)["phase"] == "planned"


def test_cross_fork_plan_requires_trunk_bases(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """A fork cannot use its lower head branch as an upstream pull request base."""
    repo, base, bottom, top = publication_repo
    _start(repo, base, head_repository="upstream/fork-project")
    _git(repo, "branch", "publish/bottom", bottom)
    _git(repo, "branch", "publish/top", top)
    _helper(repo, "record-normalized", "--tip", top)
    run_dir = Path(_helper(repo, "run-dir").stdout.strip())
    bodies = run_dir / "bodies"
    bodies.mkdir()
    (bodies / "bottom.md").write_text("Bottom\n", encoding="utf-8")
    (bodies / "top.md").write_text(
        "Top\n\nDepends on {{PRECEDING_REVIEW_URL}}.\n",
        encoding="utf-8",
    )
    plan = {
        "schema": 1,
        "base": base,
        "integration_tip": top,
        "groups": [
            {
                "id": "fork-series",
                "transport": "ordinary",
                "layers": [
                    {
                        "id": "bottom",
                        "branch": "publish/bottom",
                        "base_branch": "main",
                        "tip": bottom,
                        "title": "Publish the prerequisite",
                        "body_file": "bodies/bottom.md",
                    },
                    {
                        "id": "top",
                        "branch": "publish/top",
                        "base_branch": "publish/bottom",
                        "tip": top,
                        "title": "Publish the dependent change",
                        "body_file": "bodies/top.md",
                    },
                ],
            }
        ],
    }
    plan_path = run_dir / "plan-input.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    plan["groups"][0]["transport"] = "github-stack"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    native_result = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert native_result.returncode != 0
    assert "cannot cross a repository fork" in native_result.stderr

    plan["groups"][0]["transport"] = "ordinary"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    result = _helper(repo, "record-plan", "--file", str(plan_path), check=False)

    assert result.returncode != 0
    assert "publication layer top must target main" in result.stderr

    plan["groups"][0]["layers"][1]["base_branch"] = "main"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    _helper(repo, "record-plan", "--file", str(plan_path))
    _helper(repo, "advance", "--phase", "validated")
    _helper(repo, "advance", "--phase", "publishing")
    _confirm_push(repo, "bottom", bottom)
    _record_created_review(
        repo,
        "bottom",
        40,
        "https://github.com/upstream/project/pull/40",
        bottom,
        "main",
        base,
    )
    _confirm_push(repo, "top", top)

    request = json.loads(_helper(repo, "github-request", "--layer", "top").stdout)
    payload = json.loads(Path(request["payload_file"]).read_text(encoding="utf-8"))
    expected_body = "Top\n\nDepends on https://github.com/upstream/project/pull/40.\n"
    expected_body_sha256 = hashlib.sha256(expected_body.encode("utf-8")).hexdigest()

    assert request == {
        "base_branch": "main",
        "base_head": base,
        "body_file": f"bodies/rendered/{expected_body_sha256}.md",
        "body_sha256": expected_body_sha256,
        "endpoint": "repos/upstream/project/pulls",
        "head_branch": "publish/top",
        "head_commit": top,
        "head_repository": "upstream/fork-project",
        "head_repository_id": 202,
        "host_url": "https://github.com",
        "layer": "top",
        "payload_file": str(request["payload_file"]),
        "target_repository": "upstream/project",
        "target_repository_id": 101,
    }
    assert payload == {
        "base": "main",
        "body": expected_body,
        "draft": False,
        "head": "upstream:publish/top",
        "head_repo": "fork-project",
        "title": "Publish the dependent change",
    }


def test_start_rejects_remotely_reachable_range(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """The checkpoint must not claim history already represented by a remote ref."""
    repo, base, bottom, _top = publication_repo
    _git(repo, "update-ref", "refs/remotes/origin/topic", bottom)

    result = _helper(
        repo,
        "start",
        "--base",
        base,
        "--target-base",
        base,
        "--provider",
        "github",
        "--host-url",
        "https://github.com",
        "--target-repository",
        "upstream/project",
        "--head-repository",
        "upstream/project",
        "--target-repository-id",
        "101",
        "--head-repository-id",
        "101",
        "--remote",
        "origin",
        "--head-push-url",
        "https://example.invalid/project.git",
        "--trunk",
        "main",
        "--status",
        "ready",
        check=False,
    )

    assert result.returncode != 0
    assert "contained in the selected remote's tracking refs" in result.stderr


def test_start_ignores_refs_from_unselected_remotes(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """An unrelated configured remote must not redefine publication state."""
    repo, base, bottom, _top = publication_repo
    _git(repo, "remote", "add", "archives", "/missing/archive.git")
    _git(repo, "update-ref", "refs/remotes/archives/topic", bottom)

    checkpoint = _start(repo, base)

    assert checkpoint["remote"] == "origin"


def test_start_checks_refs_from_explicitly_selected_remote(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """An explicitly selected remote should define publication state."""
    repo, base, bottom, _top = publication_repo
    _git(repo, "remote", "rename", "origin", "archives")
    _git(repo, "update-ref", "refs/remotes/archives/topic", bottom)

    with pytest.raises(subprocess.CalledProcessError) as error:
        _start(repo, base, remote="archives")

    assert "contained in the selected remote's tracking refs" in error.value.stderr


def test_start_accepts_branch_and_remote_names_supported_by_git(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Publication should not impose narrower ref-name syntax than Git."""
    repo, base, _bottom, _top = publication_repo
    branch = "topic+plus@work"
    remote = "fork+push@home"
    trunk = "release+candidate@next"
    _git(repo, "switch", "-c", branch)
    _git(repo, "remote", "rename", "origin", remote)

    checkpoint = _start(repo, base, remote=remote, trunk=trunk)

    assert checkpoint["original_branch"] == branch
    assert checkpoint["remote"] == remote
    assert checkpoint["trunk"] == trunk


def test_start_rejects_invalid_metadata_before_creating_recovery_state(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Typed provider metadata should be preflighted before the first ref write."""
    repo, base, _bottom, _top = publication_repo

    result = _helper(
        repo,
        "start",
        "--base",
        base,
        "--target-base",
        base,
        "--provider",
        "gitlab",
        "--host-url",
        "https://gitlab.example.com",
        "--target-repository",
        "missing-namespace",
        "--head-repository",
        "group/project",
        "--remote",
        "origin",
        "--trunk",
        "main",
        "--status",
        "ready",
        check=False,
    )

    assert result.returncode != 0
    assert "target_repository" in result.stderr
    state_dir = Path(_helper(repo, "state-dir").stdout.strip())
    assert not state_dir.exists()
    assert (
        _git(
            repo,
            "for-each-ref",
            "--format=%(refname)",
            "refs/git-stage-batch/publish-unpushed-commits",
        )
        == ""
    )


def test_start_rejects_inconsistent_github_repository_ids(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """A reused repository path must not silently bind to another identity."""
    repo, base, _bottom, _top = publication_repo

    result = _helper(
        repo,
        "start",
        "--base",
        base,
        "--target-base",
        base,
        "--provider",
        "github",
        "--host-url",
        "https://github.com",
        "--target-repository",
        "upstream/project",
        "--head-repository",
        "upstream/project",
        "--target-repository-id",
        "101",
        "--head-repository-id",
        "202",
        "--remote",
        "origin",
        "--head-push-url",
        "https://example.invalid/project.git",
        "--trunk",
        "main",
        "--status",
        "ready",
        check=False,
    )

    assert result.returncode != 0
    assert "repository paths and numeric IDs disagree" in result.stderr


def test_start_rejects_a_push_remote_outside_the_head_repository(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """The selected push destination must match an API-observed clone URL."""
    repo, base, _bottom, _top = publication_repo

    result = _helper(
        repo,
        "start",
        "--base",
        base,
        "--target-base",
        base,
        "--provider",
        "github",
        "--host-url",
        "https://github.com",
        "--target-repository",
        "upstream/project",
        "--head-repository",
        "upstream/project",
        "--target-repository-id",
        "101",
        "--head-repository-id",
        "101",
        "--remote",
        "origin",
        "--head-push-url",
        "https://example.invalid/other-project.git",
        "--trunk",
        "main",
        "--status",
        "ready",
        check=False,
    )

    assert result.returncode != 0
    assert "does not match the API-observed head repository" in result.stderr


def test_start_rejects_pending_work(
    publication_repo: tuple[Path, str, str, str],
) -> None:
    """Recovery state must not bind to a dirty working tree."""
    repo, base, _bottom, _top = publication_repo
    (repo / "pending.txt").write_text("pending\n", encoding="utf-8")

    result = _helper(
        repo,
        "start",
        "--base",
        base,
        "--target-base",
        base,
        "--provider",
        "github",
        "--host-url",
        "https://github.com",
        "--target-repository",
        "upstream/project",
        "--head-repository",
        "upstream/project",
        "--target-repository-id",
        "101",
        "--head-repository-id",
        "101",
        "--remote",
        "origin",
        "--head-push-url",
        "https://example.invalid/project.git",
        "--trunk",
        "main",
        "--status",
        "ready",
        check=False,
    )

    assert result.returncode != 0
    assert "requires a clean working tree" in result.stderr
