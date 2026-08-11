"""Tests for product-backed history-refinement skill assets."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
AI_ASSISTANTS = PROJECT_ROOT / "docs" / "ai-assistants.md"
CODEX_ROOT = PROJECT_ROOT / "assets" / "codex-skills"
CLAUDE_ROOT = PROJECT_ROOT / "assets" / "claude-skills"
CODEX_HISTORY = CODEX_ROOT / "refine-history"
CLAUDE_HISTORY = CLAUDE_ROOT / "refine-history"
CODEX_MESSAGES = CODEX_ROOT / "refine-commit-messages"
CLAUDE_MESSAGES = CLAUDE_ROOT / "refine-commit-messages"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _markdown_section(contents: str, heading: str) -> str:
    """Return one level-two Markdown section, including its heading."""
    start = contents.index(f"{heading}\n")
    next_heading = contents.find("\n## ", start + len(heading))
    if next_heading == -1:
        return contents[start:]
    return contents[start : next_heading + 1]


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *arguments],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_refinement_assets_remove_skill_owned_rewrite_engines() -> None:
    """Assistant variants should not retain checkpoint or rebase engines."""
    roots = (CODEX_HISTORY, CLAUDE_HISTORY, CODEX_MESSAGES, CLAUDE_MESSAGES)
    forbidden = (
        "refine-history-checkpoint.py",
        "refine-commit-messages-checkpoint.py",
        "GIT_SEQUENCE_EDITOR=",
        "git rebase ",
        "git reset ",
        "git commit --amend",
    )

    for root in roots:
        skill = _read(root / "SKILL.md")
        for value in forbidden:
            assert value not in skill
        assert not any(root.glob("scripts/*checkpoint.py"))

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        reference = _read(root / "references" / "rewrite-procedures.md")
        for value in forbidden:
            assert value not in reference


def test_refine_history_delegates_every_mechanical_transition() -> None:
    """Boundary skills should use plans and the complete product lifecycle."""
    required = (
        "rewrite scan",
        "rewrite validate",
        "rewrite apply",
        "rewrite status",
        "rewrite continue",
        "rewrite abort",
        "rewrite verify",
        "Edit only `plan.outputs`",
        "SPLIT",
        "INTEGRATE",
        "REORDER",
        "BLOCKED",
        "UNKNOWN",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = _read(root / "SKILL.md")
        prose = " ".join(skill.split())
        for value in required:
            assert value in skill
        assert "stop after validation" in prose
        assert "Do not create refs, checkpoints, commits" in prose

    assert _read(CODEX_HISTORY / "references" / "rewrite-procedures.md") == _read(
        CLAUDE_HISTORY / "references" / "rewrite-procedures.md"
    )
    assert _read(CODEX_HISTORY / "references" / "targeted-exact-rewrites.md") == _read(
        CLAUDE_HISTORY / "references" / "targeted-exact-rewrites.md"
    )


def test_refine_history_binds_narrow_publication_scope() -> None:
    """Publication audits should consider provider-default and protected refs."""
    skill_contracts = (
        "bind an explicit run-local publication scope",
        "provider's freshly queried default branch",
        "fresh provider protected-branch query",
        "A configured upstream participates only when",
        "exactly match that provider-default binding",
        "An arbitrary feature, WIP, or review upstream remains excluded",
        "Never infer the default branch or protection from a branch name or configured upstream",
        "Require zero range overlap only against those in-scope tips",
        "Report excluded categories and observed refs separately",
        "unprotected WIP branches, tags, and archived or closed review refs",
        "Do not silently broaden the scope",
        "user or repository policy explicitly says to",
        "stop without mutation and report the executor limitation",
    )
    reference_contracts = (
        "## Bind publication scope",
        "repository's current default branch",
        "In the same fresh evidence window",
        "Resolve a configured upstream, when one exists, only as a consistency fact",
        "Do not add an arbitrary feature, WIP, or review upstream",
        "provider cannot resolve its current default branch",
        "Never infer the default branch or protection from names",
        "including any configured upstream that does not match the provider-default binding",
        "compute reachability from every commit in `BASE_SHA..HEAD` only to those bound tips",
        "unpublished only when that in-scope overlap set is empty",
        "Report each category, its observed exact refs, and why it is excluded",
        "Only an explicit user instruction or repository policy may expand",
        "Never narrow the default set",
        "fresh `safety.remote_containment`",
        "excluded containment and non-mutating result",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").split())
        reference = " ".join(
            _read(root / "references" / "rewrite-procedures.md").split()
        )
        for contract in skill_contracts:
            assert contract in skill
        for contract in reference_contracts:
            assert contract in reference
        assert "canonical configured upstream/default mainline" not in skill
        assert "A range commit contained by any unrelated remote ref" not in skill
        assert skill.index("bind an explicit run-local publication scope") < skill.index(
            "## Targeted exact rewrite"
        )
        assert reference.index("## Bind publication scope") < reference.index(
            "## Immutable and editable fields"
        )


def test_refine_history_bounds_targeted_exact_squash() -> None:
    """One adjacent squash should skip only whole-series semantic ceremony."""
    skill_contracts = (
        "## Targeted exact rewrite",
        "squash exactly two adjacent whole source commits into one",
        "does not audit or certify the rest of the series",
        "Within the trusted same-user boundary",
        "Do not add another approval step",
        "`KEEP` preservation rather than a semantic `KEEP` verdict",
        "exactly one non-`KEEP` operation",
        "`EXACT` materialization",
        "Honor an explicit `BASE_SHA`",
        "fresh full-series run",
        "Targeted mode uses the narrower rule",
        "first parent of the earliest selected source",
        "target and every descendant through `HEAD`",
        "current rewrite CLI cannot represent this root-boundary rewrite",
        "only the selected source or pair",
        "may omit the series index, causal ledger",
        "Do not run a separate `rewrite validate`",
        "validates the complete plan before creating operation state or a recovery ref",
        "operation `COMPLETE`, inactive",
        "Never use this path for `SPLIT`",
        "partial-unit integration",
        "more than one transformation",
        "`RESOLVED` materialization",
        "leave history unchanged",
    )
    full_series_contracts = (
        "one explicitly targeted reword may use the fast path below",
        "Scan writes the requested fresh plan; neither command updates commits, refs, checkpoints, an existing plan, or resolution workspaces",
        "may reuse or update the disposable history-snapshot cache",
        "For a full-series refinement, prefer one whole-range plan",
        "Before a full-series apply",
        "After any successful apply",
        "After a full-series apply",
        "When boundaries converge in a full-series refinement",
        "operation counts alone are not enough",
        "## Full-series completion gate",
        "must not be reported as a series audit",
    )
    reference_contracts = (
        "# Targeted EXACT Rewrites",
        'retain `materialization: "EXACT"` on every output',
        "Copy `EARLIER` and change its operation to `INTEGRATE`",
        "all `EARLIER` units followed by all `LATER` units",
        "user-selected boundary collapse",
        "field-for-field unchanged",
        "Do not run a separate `rewrite validate`",
        "git-stage-batch rewrite status --porcelain",
        "one fewer output commit than source commit",
        "before it creates operation state or a recovery ref",
        '`phase: "COMPLETE"`',
        "`active: false`",
        "successful completion is the product's unit and final-tree proof",
        "non-empty sources have a net-empty result",
        "never reconstruct the rewrite manually",
    )

    skill_sections = []
    reference_sections = []
    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = _read(root / "SKILL.md")
        skill_prose = " ".join(skill.split())
        for contract in (*skill_contracts, *full_series_contracts):
            assert contract in skill_prose
        assert "squash adjacent commits" in skill
        assert "Scan and validation are read-only" not in skill

        scan_section = _markdown_section(skill, "## Scan a fresh range")
        scan_recipe_end = scan_section.index("```\n\nThe scan-only recipe")
        full_series_only = scan_section.index("For a full-series\naudit")
        identity_validation = scan_section.index(
            'git-stage-batch rewrite validate "$REWRITE_PLAN"'
        )
        targeted_redirect = scan_section.index("A targeted exact rewrite instead edits")
        assert "rewrite validate" not in scan_section[:scan_recipe_end]
        assert (
            scan_recipe_end < full_series_only < identity_validation < targeted_redirect
        )

        skill_section = _markdown_section(skill, "## Targeted exact rewrite")
        assert "rewrite resolve" not in skill_section
        assert "git-stage-batch rewrite validate" not in skill_section
        assert "git-stage-batch rewrite apply" not in skill_section
        skill_section_prose = " ".join(skill_section.split())
        assert skill_section_prose.index(
            "Follow `references/targeted-exact-rewrites.md`"
        ) < skill_section_prose.index("Do not run a separate `rewrite validate`")
        skill_sections.append(skill_section)

        full_reference = _read(root / "references" / "rewrite-procedures.md")
        assert "## Build one targeted EXACT plan" not in full_reference
        reference = _read(root / "references" / "targeted-exact-rewrites.md")
        reference_prose = " ".join(reference.split())
        for contract in reference_contracts:
            assert contract in reference_prose
        assert "git-stage-batch rewrite validate" not in reference
        assert "git-stage-batch rewrite apply" in reference
        assert "`valid: true`" not in reference
        reference_sections.append(reference)

        apply_section = _markdown_section(skill, "## Apply a validated plan")
        assert apply_section.index(
            'git-stage-batch rewrite validate "$REWRITE_PLAN"'
        ) < apply_section.index('git-stage-batch rewrite apply "$REWRITE_PLAN"')

    assert skill_sections[0] == skill_sections[1]
    assert reference_sections[0] == reference_sections[1]


def test_refine_history_keeps_review_head_exception_exact() -> None:
    """Narrow scope must not turn ignored refs into apply exceptions."""
    skill_contracts = (
        "An active review head, including one configured as the current upstream, remains a narrow exception, not a scope expansion",
        "exact current review head",
        "zero overlap with the provider-default and protected scope",
        "pass only each exact full `refs/remotes/...` review-head ref",
        "Never pass an excluded WIP, tag, or archived review ref merely to clear the blocker",
    )
    targeted_contracts = (
        "exact current review-head refs authorized by the run-local publication-scope record",
        "zero overlap with its freshly queried provider-default and protected-branch tips",
        "never pass an excluded WIP branch, tag, archived or closed review ref",
        "If apply cannot express the bound scope, stop without mutation",
    )
    shared_contracts = (
        "A configured upstream that is this exact current active review head",
        "it never joins the default included set",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").split())
        targeted = " ".join(
            _read(root / "references" / "targeted-exact-rewrites.md").split()
        )
        shared = " ".join(
            _read(root / "references" / "rewrite-procedures.md").split()
        )
        for contract in skill_contracts:
            assert contract in skill
        for contract in targeted_contracts:
            assert contract in targeted
        for contract in shared_contracts:
            assert contract in shared


def test_refine_history_bounds_targeted_exact_swap() -> None:
    """One adjacent swap should retain proof and recovery boundaries."""
    skill_contracts = (
        "swap exactly two adjacent whole source commits",
        "swapped intermediate state to be coherent by inspection",
        "first output of the pair, the moved `LATER` source",
        "previously unseen intermediate tree",
        "do not claim completion or improvise a rollback",
    )
    reference_contracts = (
        "## Swap one adjacent pair",
        "Place `LATER` immediately before `EARLIER`",
        "change only `LATER`'s operation to `REORDER` and its rationale",
        "A required `BLOCKED` or `UNKNOWN` crossing",
        "first output of the pair, the moved `LATER` source",
        "`rewrite abort` cannot undo a `COMPLETE` operation",
        "Ask before any separately reviewed recovery",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill_section = _markdown_section(
            _read(root / "SKILL.md"), "## Targeted exact rewrite"
        )
        skill_prose = " ".join(skill_section.split())
        for contract in skill_contracts:
            assert contract in skill_prose
        assert skill_prose.index(
            "swapped intermediate state to be coherent by inspection"
        ) < skill_prose.index("After apply")

        reference = " ".join(
            _read(root / "references" / "targeted-exact-rewrites.md").split()
        )
        for contract in reference_contracts:
            assert contract in reference


def test_refine_history_bounds_targeted_exact_reword() -> None:
    """One complete reword should reject a byte-identical no-op."""
    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").split())
        assert "replace the complete message of exactly one source commit" in skill
        assert "one explicitly targeted reword may use the fast path below" in skill

        reference = " ".join(
            _read(root / "references" / "targeted-exact-rewrites.md").split()
        )
        assert "## Reword one source" in reference
        assert (
            "Require `(message, encoding)` to differ from the frozen source"
            in reference
        )
        assert "report that no rewrite is needed" in reference
        assert "do not apply a `REWORD` plan" in reference


def test_refine_history_documents_targeted_exact_mode() -> None:
    """Both assistant entry points should label the narrow mode honestly."""
    assistant_docs = " ".join(_read(AI_ASSISTANTS).split())
    assert assistant_docs.count("targeted exact mode") == 2
    assert (
        assistant_docs.count("does not claim to have audited the untouched series") == 2
    )


def test_refine_history_assigns_causal_ownership_before_placement() -> None:
    """Mechanical barriers must not become semantic owners or convergence."""
    skill_contracts = (
        "mandatory causal pass newest to oldest",
        "compare the commit's claim with its implementation",
        "Assign semantic ownership before consulting",
        "earliest honest semantic owner",
        'CAUSAL_LEDGER="$PLAN_DIR/causal-ledger.md"',
        "Persist that compact ledger at `CAUSAL_LEDGER`, outside the worktree",
        "Update the ledger after every validation and apply",
        "unaudited identity template",
        "Subjects are claims to test, not candidate filters",
        "Two yes answers identify a mixed source",
        "translated strings",
        "OWNED_HERE",
        "MOVE",
        "UNRESOLVED",
        "UNREPRESENTABLE",
        "never retarget it to the blocker",
        "Never apply a validated landing at a non-owner blocker",
        "original-source provenance",
        "`plan.outputs` and `plan.partitioned_units`",
        "account for every declared partitioned occurrence",
        "use the reported recovery ref and read-only Git inspection to reconstruct original-source provenance",
        "missing provenance as `UNRESOLVED`",
        "leave every fresh semantic group `OWNED_HERE`",
        "compound movement may cross a complete `BLOCKED` chain only when all grouped units share the same semantic outcome",
        "necessary mechanical proof, not sufficient semantic proof",
    )
    reference_contracts = (
        "## Assign ownership, prerequisites, and placement",
        "A mechanical blocker is a placement frontier, never an owner",
        "four non-plan audit states",
        "not necessarily a semantic atom",
        "strictly increasing `output_indexes`",
        "later residual SPLIT output",
        "repeat it only in the affected RESOLVED outputs",
        "Never merge an unrelated blocker chain",
        "An ungrouped `BLOCKED` edge",
        "A later fresh scan remaps it to fresh unit IDs",
        "Never apply a non-owner blocker landing as a stepping stone",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = _read(root / "SKILL.md")
        skill_prose = " ".join(skill.split())
        for contract in skill_contracts:
            assert contract in skill_prose
        assert "or keep the existing boundary" not in skill_prose
        assert skill_prose.index(
            "Assign semantic ownership before consulting"
        ) < skill_prose.index("After ownership, perform a mandatory ordering pass")

        reference = _read(root / "references" / "rewrite-procedures.md")
        reference_prose = " ".join(reference.split())
        for contract in reference_contracts:
            assert contract in reference_prose
        assert "current schema cannot divide it" not in reference_prose
        assert (
            "cannot make one source both an integrated secondary" not in reference_prose
        )


def test_refine_history_uses_identifiable_durable_scratch() -> None:
    """Long-running plan artifacts should avoid the ordinary /tmp default."""
    required = (
        "PLAN_PARENT=${TMPDIR:-${TEMP:-${TMP:-}}}",
        'test "$(uname -s)" = Linux',
        "PLAN_PARENT=/var/tmp",
        'PLAN_DIR=$(mktemp -d "$PLAN_PARENT/git-stage-batch-refine-history.XXXXXXXX")',
        "PLAN_DIR=$(mktemp -d)",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = _read(root / "SKILL.md")
        for value in required:
            assert value in skill
    assert "Bash(uname *)" in _read(CLAUDE_HISTORY / "SKILL.md")


def test_refine_history_scratch_honors_environment_and_platform(
    tmp_path: Path,
) -> None:
    """The skill scratch policy should match the runtime selection order."""
    configured = {
        variable: tmp_path / variable.lower()
        for variable in ("TMPDIR", "TEMP", "TMP")
    }
    cases = (
        (
            "Linux",
            configured,
            f"-d {configured['TMPDIR']}/git-stage-batch-refine-history.XXXXXXXX",
        ),
        (
            "Linux",
            {key: value for key, value in configured.items() if key != "TMPDIR"},
            f"-d {configured['TEMP']}/git-stage-batch-refine-history.XXXXXXXX",
        ),
        (
            "Linux",
            {"TMP": configured["TMP"]},
            f"-d {configured['TMP']}/git-stage-batch-refine-history.XXXXXXXX",
        ),
        ("Linux", {}, "-d /var/tmp/git-stage-batch-refine-history.XXXXXXXX"),
        ("Darwin", {}, "-d"),
    )
    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill_lines = _read(root / "SKILL.md").splitlines()
        start = next(
            index
            for index, line in enumerate(skill_lines)
            if line.startswith("PLAN_PARENT=")
        )
        end = next(
            index
            for index, line in enumerate(skill_lines[start:], start)
            if line.startswith("REWRITE_PLAN=")
        )
        commands = "\n".join(skill_lines[start:end])
        for platform, overrides, expected_arguments in cases:
            environment = os.environ.copy()
            for variable in ("TMPDIR", "TEMP", "TMP"):
                environment.pop(variable, None)
            environment.update(
                {key: str(value) for key, value in overrides.items()}
            )
            result = subprocess.run(
                [
                    "bash",
                    "-eu",
                    "-c",
                    (
                        f"uname() {{ printf '%s\\n' {platform!r}; }}\n"
                        "mktemp() { printf '%s\\n' \"$*\"; }\n"
                        f"{commands}\n"
                        'printf \'%s\\n\' "$PLAN_DIR"'
                    ),
                ],
                check=True,
                capture_output=True,
                text=True,
                env=environment,
            )
            assert result.stdout.strip() == expected_arguments


def test_refine_history_uses_placement_to_order_after_ownership() -> None:
    """Placement should constrain chronology without becoming ownership."""
    skill_steps = (
        "Assign semantic ownership before consulting",
        "After ownership, perform a mandatory ordering pass",
        "Add the semantic prerequisite edges",
        "Overlay `earliest_position`, `BLOCKED`, `UNKNOWN`, and exact replay evidence",
        "Choose the earliest feasible chronology",
        "Use `EXACT` when every required crossing is proven",
        "Use `RESOLVED` when the required owner or prerequisite order is noncommuting",
    )
    reference_steps = (
        "Fix that causal owner before choosing an output position",
        "determine the semantic prerequisite edges",
        "overlay the scan's `earliest_position`, `BLOCKED`, `UNKNOWN`, and exact replay evidence",
        "Choose the earliest feasible chronology",
        "Use `EXACT` for an output when every crossing required by that chronology is proven",
        "Use `RESOLVED` when the owner or prerequisite order requires a noncommuting placement",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").replace("\\\n", " ").split())
        skill_positions = [skill.index(step) for step in skill_steps]
        assert skill_positions == sorted(skill_positions)
        assert (
            "Mechanical placement therefore selects and constrains chronology" in skill
        )
        assert "it never reassigns ownership" in skill

        reference = " ".join(
            _read(root / "references" / "rewrite-procedures.md")
            .replace("\\\n", " ")
            .split()
        )
        reference_positions = [reference.index(step) for step in reference_steps]
        assert reference_positions == sorted(reference_positions)
        assert "placement evidence constrains and selects the chronology" in reference
        assert "it never changes the owner" in reference


def test_refine_history_audits_resolved_path_chronology() -> None:
    """Resolved paths should make owner-correct chronological progress."""
    skill_contracts = (
        "actual parent tree",
        "every later natural source/path transition",
        "hidden stepping stone",
        "change the output topology",
        "one progressive chronology",
        "Never resolve occurrences independently from `SOURCE_BEFORE`",
        "no-op occurrence",
    )
    reference_contracts = (
        "`CURRENT_PARENT`",
        "`SOURCE_BEFORE`",
        "`SOURCE_AFTER`",
        "every later natural source boundary",
        "hidden stepping stone",
        "one progressive transition chain",
        "Never resolve occurrences independently from `SOURCE_BEFORE`",
        "no-op occurrence",
        "change the output topology",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").split())
        for contract in skill_contracts:
            assert contract in skill
        assert skill.index("actual parent tree") < skill.index(
            "every later natural source/path transition"
        )
        assert skill.index("one progressive chronology") < skill.index(
            "Never resolve occurrences independently from `SOURCE_BEFORE`"
        )

        reference = " ".join(
            _read(root / "references" / "rewrite-procedures.md").split()
        )
        for contract in reference_contracts:
            assert contract in reference
        assert reference.index("`CURRENT_PARENT`") < reference.index(
            "every later natural source boundary"
        )
        assert reference.index("one progressive transition chain") < reference.index(
            "Never resolve occurrences independently from `SOURCE_BEFORE`"
        )


def test_refine_history_continues_from_verified_prefixes() -> None:
    """Verified suffixes should become fresh, portable refinement input."""
    skill_contracts = (
        "verified prefix for later direct child commits",
        "binding its canonical base, tip and tree, ordered commit/tree vector",
        "record its earlier causal owner in a new external ledger",
        "neither changes semantic ownership nor permits editing a completed checkpoint",
        "fresh `rewrite scan` from the same canonical base through the new tip",
        "suffix is new refinement input",
        "not an active product operation that `rewrite continue` can resume",
        "Do not rebuild the authenticated prefix or recreate a completed `RESOLVED` workspace",
        "prefix outputs are ordinary immutable sources",
        "new `RESOLVED` output only for a demonstrated dependency or topology constraint",
        "disposable ordinary clone with no alternates",
        "Reacquire every repository-bound scan or resolution workspace",
        "unique immutable run root",
        "exact command, environment, exit status, diagnosed cause",
        "never retry in place, overwrite the failed evidence",
        "Do not blindly repeat an unchanged command and environment",
        "failed suffix attempt does not invalidate",
        "only a clean receipt may authorize continuation",
    )
    reference_contracts = (
        "## Continue from a verified prefix",
        "selected semantic-check receipts",
        "linear chain whose first parent is that exact prefix",
        "new external causal ledger anchored at the verified prefix",
        "Never reopen or edit the checkpoint that completed the prefix",
        "fresh scan from the same canonical base through the appended tip",
        "suffix is new input",
        "never a reason to call `rewrite continue`",
        "Do not rebuild verified-prefix commits or recreate a completed `RESOLVED` workspace",
        "prefix outputs become ordinary immutable sources",
        "demonstrated dependency or topology constraint",
        "disposable ordinary clone with no alternates",
        "Never copy a repository-bound plan or workspace",
        "new immutable run root",
        "exact command, environment, exit status, diagnosed cause",
        "Do not retry in place",
        "blindly repeat the same command and environment",
        "failed attempt remains negative evidence",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").split())
        for contract in skill_contracts:
            assert contract in skill
        assert skill.index("verified prefix for later direct child commits") < skill.index(
            "fresh `rewrite scan` from the same canonical base through the new tip"
        )
        assert skill.index("fresh `rewrite scan`") < skill.index(
            "prefix outputs are ordinary immutable sources"
        )
        assert skill.index("disposable ordinary clone with no alternates") < (
            skill.index("unique immutable run root")
        )

        reference = " ".join(
            _read(root / "references" / "rewrite-procedures.md").split()
        )
        for contract in reference_contracts:
            assert contract in reference
        assert reference.index("## Continue from a verified prefix") < reference.index(
            "fresh scan from the same canonical base through the appended tip"
        )


def test_refine_history_stabilizes_and_recovers_harness_execution() -> None:
    """Harnesses should bind full IDs and adopt landed commits safely."""
    skill_contracts = (
        "never depend on Git's automatic `%h` abbreviation",
        "`core.abbrev=40` in every Git and product child environment",
        "two ordinary no-alternates clones",
        "materially different cardinalities",
        "identical full-ID object graphs",
        "automatic abbreviations repository-independent",
        "commit lands but a later harness gate fails before its verification or checkpoint record",
        "Do not reset, amend, or recommit the exact landed commit",
        "binding its HEAD, parent, tree, index, refs, reflogs, hook transcript",
        "exact object-namespace delta",
        "failure occurred after commit creation but before verification and checkpoint publication",
        "new checkpoint lineage with an explicit adoption record",
        "mark the adopted commit verified before creating another commit",
        "Any mismatch requires manual recovery",
    )
    reference_contracts = (
        "## Stabilize and recover harness execution",
        "must not consume automatic `%h` abbreviations",
        "Force `core.abbrev=40` through every direct Git invocation",
        "every product child's Git environment",
        "assert the effective setting and full-length log output before mutation",
        "two ordinary no-alternates clones",
        "materially different object-store cardinalities",
        "unpinned automatic abbreviations differ",
        "landed-state recovery, not permission to retry",
        "do not reset, amend, or recommit its exact HEAD",
        "authenticates the failed-run inventory",
        "refs, reflog suffixes, hook transcript",
        "object-namespace delta are exactly the landed state",
        "`ADOPTED_PENDING_VERIFICATION`",
        "records `VERIFIED` before it creates the next commit",
        "require manual recovery instead",
    )

    skill_sections: list[str] = []
    reference_sections: list[str] = []
    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").split())
        for contract in skill_contracts:
            assert contract in skill
        assert skill.index("core.abbrev=40") < skill.index(
            "commit lands but a later harness gate fails"
        )
        start = skill.index("When an execution harness authenticates")
        end = skill.index("For a plan with `RESOLVED` outputs", start)
        skill_sections.append(skill[start:end])

        reference = " ".join(
            _markdown_section(
                _read(root / "references" / "rewrite-procedures.md"),
                "## Stabilize and recover harness execution",
            ).split()
        )
        for contract in reference_contracts:
            assert contract in reference
        assert reference.index("core.abbrev=40") < reference.index(
            "landed-state recovery"
        )
        reference_sections.append(reference)

    assert skill_sections[0] == skill_sections[1]
    assert reference_sections[0] == reference_sections[1]


def test_refine_history_uses_risk_tiered_proof() -> None:
    """Full refinement should bind exhaustive objects to scoped commands."""
    skill_contracts = (
        "Use three verification tiers",
        "Object and plan",
        "audit every output, including its parent, tree, message, author, encoding, operation",
        "source-unit and path ownership, output order, original-source provenance",
        "Semantic boundaries",
        "maps each selected exact output to its risk and exact commands",
        "Each command must exist at that snapshot",
        "all commands for one output in one clean checkout",
        "generated state only within that group and keeping it outside the checkout",
        "Final tip",
        "complete normal repository test suite",
        "Reauthenticate the complete combined output chain",
        "boundary selection tied to recorded risk",
    )
    reference_contracts = (
        "Verify a full-series result in three tiers",
        "object-and-plan tier audits every output's commit, parent, tree, message, author, encoding, operation",
        "source-unit and path ownership, output order, original-source provenance",
        "semantic-boundary tier",
        "maps each exact selected output to its risk and exact commands",
        "every command to exist at that snapshot",
        "all commands for one output in one clean checkout",
        "generated state only within that group and storing it outside the checkout",
        "final-tip tier runs the complete normal repository test suite",
        "every output in the complete combined chain",
        "follows its exact risk manifest",
    )

    prohibited = (
        "every commit snapshot",
        "every final commit snapshot",
        "full commit-snapshot command matrix",
        "command matrix over every commit",
        "every-commit command requirement",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").split())
        for contract in skill_contracts:
            assert contract in skill
        assert skill.index("Object and plan") < skill.index("Semantic boundaries")
        assert skill.index("Semantic boundaries") < skill.index("Final tip")

        reference = " ".join(
            _read(root / "references" / "rewrite-procedures.md").split()
        )
        for contract in reference_contracts:
            assert contract in reference
        assert reference.index("object-and-plan tier") < reference.index(
            "semantic-boundary tier"
        )
        assert reference.index("semantic-boundary tier") < reference.index(
            "final-tip tier"
        )

        for phrase in prohibited:
            assert phrase not in skill.lower()
            assert phrase not in reference.lower()

    public_docs = " ".join(_read(AI_ASSISTANTS).split())
    assert "audits every output and its plan" in public_docs
    assert "exact risk-selected semantic-boundary manifest" in public_docs
    assert "completes the final-tip test and build suite" in public_docs
    for phrase in prohibited:
        assert phrase not in public_docs.lower()


def test_refine_history_flows_completed_resolutions_through_apply() -> None:
    """Resolved plans should retain one plan binding through apply."""
    skill_contracts = (
        'REWRITE_WORKSPACE="$PLAN_DIR/rewrite-workspace"',
        'rewrite resolve "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"',
        "until the product reports `COMPLETE`",
        'rewrite validate "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"',
        'rewrite apply "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"',
        "copies it into operation-owned state before activation",
        "do not depend on the external workspace remaining present",
        "`inspection.resolution_matches`",
        "`inspection.resolution_matches` must be null for an all-`EXACT` operation or true for a resolved operation",
        "false blocks continuation",
    )
    reference_contracts = (
        "## Resolve, validate, apply, and recover",
        'rewrite resolve "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"',
        "until the workspace reports `COMPLETE`",
        'rewrite validate "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"',
        'rewrite apply "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"',
        "copies it into operation-owned state before activation",
        "do not depend on the external workspace remaining present",
        "`inspection.resolution_matches`",
        "`inspection.resolution_matches` is null for an all-`EXACT` operation or true for a resolved operation",
        "A false resolution match blocks continuation",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").replace("\\\n", " ").split())
        for contract in skill_contracts:
            assert contract in skill
        assert skill.index('rewrite resolve "$REWRITE_PLAN"') < skill.index(
            'rewrite validate "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"'
        )
        assert skill.index(
            'rewrite validate "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"'
        ) < skill.index('rewrite apply "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"')

        reference = " ".join(
            _read(root / "references" / "rewrite-procedures.md")
            .replace("\\\n", " ")
            .split()
        )
        for contract in reference_contracts:
            assert contract in reference
        assert reference.index('rewrite resolve "$REWRITE_PLAN"') < reference.index(
            'rewrite validate "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"'
        )
        assert reference.index(
            'rewrite validate "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"'
        ) < reference.index(
            'rewrite apply "$REWRITE_PLAN" --workspace "$REWRITE_WORKSPACE"'
        )


def test_refine_history_reuses_trusted_correctness_evidence() -> None:
    """History refinement should preserve causal ownership and reusable facts."""
    skill_contracts = (
        "one trusted same-user execution boundary",
        "stale-state detection, tree conservation, atomic updates, and recovery",
        "Do not add a second approval format",
        "custom Python module loader",
        "chained helper manifest",
        "parallel workspace-transfer transaction",
        "demonstrated correctness, consistency, atomicity, or recovery requirement",
        "source-wide placement evidence",
        "never overrides an established semantic owner",
        "Retain `RESOLVED`",
        "retain the intended owner as `UNREPRESENTABLE`",
        "independent causal evidence changes its owner",
        "documented `scan`, resolve or validate, apply, and verify workflow",
        "Avoid duplicate product invocations for the same state",
        "reuse exact immutable commit, tree, unit, and dependency evidence",
        "Memoize semantic-audit conclusions",
        "content-addressed keys",
        "invalidate and re-audit only its cone",
        "affected units, owners, prerequisite dependents, and output snapshots",
        "persistently caches immutable snapshot and dependency analysis",
        "collecting live safety facts again",
        "never treat a cache hit as plan, replay, or final-range proof",
        "Incremental suffix scans, replay-tree caching, and Bloom-filter path prefilters are not implemented",
        "never use a probabilistic prefilter as correctness proof",
        "Any plan-file byte change after workspace creation invalidates use of the prior workspace binding",
        "A semantic edit to `plan.outputs` or `plan.partitioned_units` additionally invalidates",
        "does not itself invalidate immutable Git tree or object IDs",
        "unchanged ID remains content-addressed evidence",
        "expected output-count or operation-count delta",
        "fresh base or fresh unit IDs alone are not progress",
        "changed owner output, its immediate adopter or test successor",
        "later natural source or test boundary",
    )
    reference_contracts = (
        "re-audit every moved unit from that source",
        "rather than changing only the rejected output",
        "Treat the rejection as placement evidence",
        "never overrides established causal ownership",
        "Retain `RESOLVED`",
        "open `UNREPRESENTABLE` finding with the exact diagnostic",
        "infer a new owner from placement failure",
        "resolved replay bound to the current plan",
        "workspace binding ties the fresh plan",
        "Each request's `output_key` also binds",
        "exact `parent_tree`",
        "declared path/artifact inventory",
        "result digest, artifact digests",
        "immediately replay every intervening `EXACT` output",
        "Any plan-file byte change after workspace creation invalidates use of the prior workspace binding",
        "A semantic change to either editable plan field additionally invalidates",
        "does not itself invalidate immutable Git tree or object IDs",
        "Product resolve and validation replay are the authoritative tree proof",
        "Only to diagnose an unexplained request, result, receipt, or replay-tree discrepancy",
        "temporary Git index",
        "declared path/mode transitions",
        "temporary Git object store",
        "Do not derive that diagnostic tree from a checkout or filesystem walk",
        "The diagnostic never replaces product validation",
        "Fresh unit IDs or a fresh base with unchanged ownership decisions are not progress",
        "A passing final tip does not replace those intermediate checks",
        "persistent history-snapshot cache reuses exact commit, tree, unit, and dependency records",
        "Git behavior, and analysis versions match",
        "Live safety facts are collected again on every command",
        "never edit or copy a cache entry into a plan",
        "re-audit the affected dependency and snapshot cone",
        "Retain the final whole-range validation",
        "no Bloom-filter path prefilter is implemented",
    )
    superseded_skill_phrases = (
        "private external workspace",
        "authenticated completed workspace",
        "reauthenticates the completed external workspace",
        "missing step from Git state or private files",
        "one scan/validator/continue process authoritative",
        "invalidates all request keys, results, receipts, parent/output tree IDs",
    )
    superseded_reference_phrases = (
        "authenticated resolved replay",
        "private external workspace",
        "Apply authenticates the external workspace",
        "private plan/state files",
        "reopens ownership, prerequisites, partitioning, and chronology",
        "Changing either editable plan field invalidates all request keys",
        "For an independent candidate-tree check",
    )

    for root in (CODEX_HISTORY, CLAUDE_HISTORY):
        skill = " ".join(_read(root / "SKILL.md").split())
        for contract in skill_contracts:
            assert contract in skill
        for phrase in superseded_skill_phrases:
            assert phrase not in skill
        assert skill.index("one trusted same-user execution boundary") < skill.index(
            "Edit only `plan.outputs`"
        )
        assert skill.index("source-wide placement evidence") < skill.index(
            "Classify intended outputs"
        )
        assert skill.index(
            "documented `scan`, resolve or validate, apply, and verify workflow"
        ) < skill.index("At each snapshot")
        assert skill.index("Memoize semantic-audit conclusions") < skill.index(
            "At each snapshot"
        )
        assert skill.index(
            "never overrides an established semantic owner"
        ) < skill.index("Retain `RESOLVED`")
        assert skill.index(
            "expected output-count or operation-count delta"
        ) < skill.index("Never apply a validated landing")
        assert skill.index("changed owner output") < skill.index(
            "When boundaries converge"
        )

        reference = " ".join(
            _read(root / "references" / "rewrite-procedures.md").split()
        )
        for contract in reference_contracts:
            assert contract in reference
        for phrase in superseded_reference_phrases:
            assert phrase not in reference
        assert reference.index(
            "workspace binding ties the fresh plan"
        ) < reference.index('rewrite resolve "$REWRITE_PLAN"')
        assert reference.index(
            "immediately replay every intervening `EXACT` output"
        ) < reference.index("A semantic change to either editable plan field")
        assert reference.index(
            "Product resolve and validation replay are the authoritative tree proof"
        ) < reference.index("temporary Git index")
        assert reference.index("temporary Git index") < reference.index(
            "Validation does not update commits, refs, checkpoints"
        )


def test_message_refinement_is_a_message_only_history_plan() -> None:
    """Message skills should validate their allowed subset before resume."""
    for root in (CODEX_MESSAGES, CLAUDE_MESSAGES):
        skill = _read(root / "SKILL.md")
        prose = " ".join(skill.split())
        assert "only `KEEP` and `REWORD`" in skill
        assert "`plan.operation_counts`" in skill
        assert "rewrite scan" in skill
        assert "rewrite validate" in skill
        assert "rewrite apply" in skill
        assert "rewrite continue" in skill
        assert "rewrite abort" in skill
        assert "rewrite verify" in skill
        assert "installed `rewrite --help` wins" in prose
        assert "history --help" not in skill
        assert "constructs deterministic unsigned commits" in skill
        assert "signature header by audited digest" in skill
        assert "Audit mode must not call `rewrite apply`" in skill

    codex = _read(CODEX_MESSAGES / "SKILL.md")
    claude = _read(CLAUDE_MESSAGES / "SKILL.md")
    assert "$refine-commit-messages audit BASE_SHA" in codex
    assert "/refine-commit-messages audit BASE_SHA" in claude
    assert "--audit-only" not in codex
    assert "--audit-only" not in claude


def test_message_refinement_discloses_snapshot_cache() -> None:
    """Message skills should qualify immutable-analysis cache writes."""
    for root in (CODEX_MESSAGES, CLAUDE_MESSAGES):
        prose = " ".join(_read(root / "SKILL.md").split())
        assert "may reuse or update the disposable history-analysis cache" in prose
        assert "Scan and validation are read-only" not in prose


def test_message_guidance_requires_low_context_prose() -> None:
    """Drafters should explain repository terms instead of inventing shorthand."""
    for root in (CODEX_MESSAGES, CLAUDE_MESSAGES):
        guidance = _read(root / "references" / "message-guidelines.md")
        assert "## Low-context prose" in guidance
        assert "Do not invent a one- or two-word name" in guidance
        assert "Define a codebase-specific or ambiguous term at first use" in guidance
        assert "Make each message independently understandable" in guidance
        assert "Apply a read-once test" in guidance

    drafters = (
        CODEX_ROOT / "internal" / "commit-message-drafter.md",
        PROJECT_ROOT / "assets" / "claude-agents" / "commit-message-drafter.md",
    )
    for path in drafters:
        drafter = " ".join(_read(path).split())
        assert "Do not reread the complete raw series" in drafter
        assert "Write for a reader who has never seen the repository" in drafter


def test_snapshot_helpers_match_and_leave_no_worktree(
    tmp_path: Path,
) -> None:
    """The retained semantic checker should clean up its detached worktree."""
    codex_helper = CODEX_HISTORY / "scripts" / "verify-head-snapshot.py"
    claude_helper = CLAUDE_HISTORY / "scripts" / "verify-head-snapshot.py"
    assert codex_helper.read_bytes() == claude_helper.read_bytes()

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.name", "Test User")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "value.txt").write_text("committed\n", encoding="utf-8")
    _git(repo, "add", "value.txt")
    _git(repo, "commit", "-m", "Base")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    observed_worktree = tmp_path / "observed-worktree"

    result = subprocess.run(
        [
            sys.executable,
            str(codex_helper),
            "--repo",
            str(repo),
            "--ref",
            "HEAD",
            "--",
            sys.executable,
            "-c",
            (
                "from pathlib import Path; "
                "assert Path('value.txt').read_text() == 'committed\\n'; "
                f"Path({str(observed_worktree)!r}).write_text(str(Path.cwd()))"
            ),
        ],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "TMPDIR": str(scratch)},
    )

    assert result.returncode == 0, result.stderr
    snapshot_worktree = Path(observed_worktree.read_text(encoding="utf-8"))
    assert snapshot_worktree.parent.parent == scratch
    assert snapshot_worktree.parent.name.startswith("git-stage-batch-verify-head-")
    worktrees = _git(repo, "worktree", "list", "--porcelain").stdout
    assert worktrees.count("worktree ") == 1
    assert _git(repo, "status", "--short").stdout == ""
