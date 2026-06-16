---
name: decompose-and-commit-unstaged-changes
description: Decompose unstaged working-tree changes into narrow concerns, peel them into git-stage-batch batches, rebuild as a fine-grained atomic commit series, or polish an already-built series
user-invocable: true
disable-model-invocation: true
context: fork
when_to_use: "Use when the user wants to decompose unstaged working-tree changes into a clean commit series by identifying product, workflow, or architectural concerns, peeling them away one by one into concern-named batches, reducing the working tree to a minimal base, and then rebuilding by applying batches in reverse order with fine-grained commits inside each concern. Also use history-polish mode when the final tree is already committed and the user wants to split, reword, or integrate commits so the existing series reads like a clean incremental evolution. Examples: \"decompose and commit unstaged changes\", \"decompose into a commit series\", \"peel this into layered commits\", \"build this up brick by brick\", \"history-polish this series\"."
allowed-tools:
  - Read
  - Grep
  - Glob
  - LS
  - Agent(decompose-analyzer)
  - Agent(decompose-deconstructor)
  - Agent(decompose-rebuilder)
  - Bash(git *)
  - Bash(git-stage-batch *)
  - Bash(pipx run git-stage-batch *)
  - Bash(python *)
  - Bash(mkdir *)
  - Bash(test *)
  - Bash(ls *)
---

# Decompose and Commit Unstaged Changes

Orchestrate the decomposition of unstaged working-tree changes into a clean
commit series through three phases, each delegated to a specialized agent.
When requested, polish an already-built commit series without re-running the
decomposition phases.

## Usage

```text
/decompose-and-commit-unstaged-changes
/decompose-and-commit-unstaged-changes deconstruct
/decompose-and-commit-unstaged-changes reconstruct
/decompose-and-commit-unstaged-changes resume
/decompose-and-commit-unstaged-changes history-polish BASE_SHA
```

Without an argument, run all three phases end to end. With `deconstruct`,
run Phases 1-2 only. With `reconstruct`, assume batches already exist and
run Phase 3 only. With `resume`, inspect the workspace-local workflow state
directory, batch refs, and the current `HEAD`, then continue from the latest
phase whose gate can still be proven. With `history-polish`, assume the final
tree is already committed and rewrite only the existing commit series between
the explicit `BASE_SHA` and `HEAD`; do not read an old checkpoint to choose the
base for a fresh history-polish run.

This skill is autonomous and non-interactive. Do not ask the user to review
intermediate steps.

If `git-stage-batch` is available directly in `PATH`, use it. If not, fall
back to `pipx run git-stage-batch`.

Before using any `git-stage-batch` command, read the installed command
documentation for the top-level command and every subcommand you intend to
use. The installed man pages/help output are the authority. Do not invent
subcommands, selectors, flags, or argument shapes from memory.

```bash
git-stage-batch --help
git-stage-batch start --help
git-stage-batch show --help
git-stage-batch status --help
git-stage-batch include --help
git-stage-batch discard --help
git-stage-batch apply --help
git-stage-batch reset --help
git-stage-batch again --help
git-stage-batch stop --help
git-stage-batch list --help
git-stage-batch drop --help
git-stage-batch block-file --help
git-stage-batch suggest-fixup --help
```

If a command or option is not shown by the installed documentation, do not use
it. If the skill text and installed help disagree, follow the installed help
and report the discrepancy.

Use the checkpoint helper for every mode. First move to the repository root,
create the workspace-local state directory, and locally block it from
`git-stage-batch` review:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
export DECOMPOSE_STATE_DIR=$(python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py state-dir)
mkdir -p "$DECOMPOSE_STATE_DIR"
git-stage-batch block-file --local-only .git-stage-batch/
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py status
```

Before a full run or a `deconstruct` run, check for stale batch state:

```bash
git-stage-batch status
git-stage-batch list
```

If a session is active or `git-stage-batch list` shows preexisting
`decompose-*` batches, stop and report the stale state before Phase 1. Do not
fold old batches into a new plan. In `reconstruct` mode, existing batches are
allowed only because they are the requested input, and Gate 2 must still pass.
In `resume` mode, existing batches are potential checkpoint state, not stale
by default. They must still pass Gate 2 before Phase 3.

Before a full run or a `deconstruct` run, also treat any existing
`decompose-plan.json` and `decompose-narrative.md` in the workflow state
directory as stale output from another attempt. Phase 1 must not read them as
input. Remove any old candidate and narrative files before analysis; the
final plan is overwritten only after the candidate passes Gate 1:

```bash
python - <<'PY'
import subprocess
from pathlib import Path
state_dir = Path(subprocess.check_output(
    ["python", ".claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py", "state-dir"],
    text=True,
).strip())
state_dir.mkdir(parents=True, exist_ok=True)
(state_dir / "decompose-plan.candidate.json").unlink(missing_ok=True)
(state_dir / "decompose-plan.json").unlink(missing_ok=True)
(state_dir / "decompose-narrative.md").unlink(missing_ok=True)
(state_dir / "decompose-refinement.md").unlink(missing_ok=True)
PY
```

For a fresh full or `deconstruct` run, record the new checkpoint immediately
after recording the base commit:

```bash
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py start --mode full --base "$BASE_SHA"
```

Use `--mode deconstruct` instead of `--mode full` for a `deconstruct` run.

## Resume Mode

`resume` is conservative. It may reuse artifacts only after re-running the
gate that proves those artifacts are valid for the current tree.

Start with:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
export DECOMPOSE_STATE_DIR=$(python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py state-dir)
mkdir -p "$DECOMPOSE_STATE_DIR"
git-stage-batch block-file --local-only .git-stage-batch/
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py status --json
git --no-optional-locks status --short
git --no-optional-locks log --oneline -20
```

Then choose the resume point:

1. If `resume_target` is `gate1`, rerun Gate 1 against
   `$DECOMPOSE_STATE_DIR/decompose-plan.candidate.json`. If Gate 1 passes,
   promote the plan and continue to Phase 2. If Gate 1 fails, discard the
   candidate/narrative as stale analysis output and rerun Phase 1.
2. If `resume_target` is `phase2-after-gate1`, rerun Gate 1 against the
   current plan. If it passes, continue Phase 2. If it fails, rerun Phase 1.
3. If `resume_target` is `phase3-after-gate2`, rerun Gate 2 from refs. If it
   passes, continue Phase 3 with remaining batches. If it fails, return to
   Phase 2 and fix the batch plan before rebuilding.
4. If `resume_target` is `gate3-or-manual-audit`, rerun Gate 3 and the
   committed-snapshot verification loop before reporting success. If any
   batch refs remain, prefer `phase3-after-gate2` instead.
5. If `resume_target` is `fresh`, run the normal full workflow from Phase 1.

Do not treat a candidate plan plus narrative as sufficient progress by
itself. Candidate artifacts are resumable only through Gate 1. A failed Gate 1
means the prior analysis was not a checkpoint; it was a rejected draft.

After every successful gate or phase transition, run:

```bash
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase PHASE-NAME
```

## History-Polish Mode

`history-polish` is a targeted rewrite mode for a series that has already been
rebuilt and committed. It does not run Phase 1 analysis, Phase 2 deconstruction,
or Phase 3 batch application. Its only job is to improve the existing commit
series so it reads like a natural incremental evolution while preserving the
final tree exactly.

Use this mode when the final `HEAD` tree is correct but the series still has
broad snapshot commits, late repair/process commits, generic artifact-shaped
subjects, docs-before-code commits, implementation-only runs followed by
test-only runs, or other narrative problems. This mode is also the resumable
entry point for rerunning only the final split and repair-integration stages
after an earlier full run was interrupted.

Start from the repository root, block workflow state from batch review, and
read the installed `git-stage-batch` help before using any batch command:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
export DECOMPOSE_STATE_DIR=$(python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py state-dir)
mkdir -p "$DECOMPOSE_STATE_DIR"
git-stage-batch block-file --local-only .git-stage-batch/
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py status --json
git-stage-batch --help
git-stage-batch start --help
git-stage-batch show --help
git-stage-batch include --help
git-stage-batch stop --help
git-stage-batch status --help
git-stage-batch list --help
```

Determine the base of the series from the explicit `BASE_SHA` argument. Fresh
`history-polish` must not read an old checkpoint, `history-polish-*` file,
narrative, plan, audit, or review artifact before the fresh start step clears
the state directory. Existing state files are contaminated inputs from another
attempt, not context for the current polish run. If no base argument was
supplied, stop and ask for `BASE_SHA`; do not guess from branch names, old
refs, reflog entries, or checkpoint state.

```bash
if test -z "${BASE_SHA:-}"; then
  echo "history-polish requires BASE_SHA; rerun as history-polish BASE_SHA"
  exit 1
fi
git --no-optional-locks merge-base --is-ancestor "$BASE_SHA" HEAD
```

Require a clean tree and no stale batch session before rewriting history:

```bash
git --no-optional-locks status --short
git-stage-batch list
git-stage-batch status
```

If any command reports pending work, active state, or stale `decompose-*`
batches, stop and report the blocker. Do not start history polishing while
ordinary working-tree changes or old batch refs are present.

Start a fresh checkpoint before reading or writing any state artifacts. The
start command clears stale files in `.git-stage-batch/` for non-resume modes;
that cleanup is intentional. Only after that fresh start should this run record
the tree and series it is about to preserve:

```bash
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py start --mode history-polish --base "$BASE_SHA"
git --no-optional-locks rev-parse HEAD^{tree} > "$DECOMPOSE_STATE_DIR/history-polish-pre-tree.txt"
git --no-optional-locks rev-list --count "$BASE_SHA"..HEAD > "$DECOMPOSE_STATE_DIR/history-polish-pre-count.txt"
git --no-optional-locks log --reverse --format='%H %s' "$BASE_SHA"..HEAD > "$DECOMPOSE_STATE_DIR/history-polish-pre-series.txt"
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase history-polish-running --note "starting history polish"
```

Before judging commits, generate a pressure list of commits that are presumed
split candidates. Do not rely on intuition or subject lines alone:

```bash
python - "$BASE_SHA" <<'PY' > "$DECOMPOSE_STATE_DIR/history-polish-pressure-list.txt"
import re
import subprocess
import sys

base = sys.argv[1]
log = subprocess.check_output(
    ["git", "--no-optional-locks", "log", "--reverse", "--format=%H%x00%s", f"{base}..HEAD"],
    text=True,
)
for line in log.splitlines():
    if not line:
        continue
    sha, subject = line.split("\x00", 1)
    stat = subprocess.check_output(
        ["git", "--no-optional-locks", "show", "--shortstat", "--format=", "--find-renames", sha],
        text=True,
    )
    names = subprocess.check_output(
        ["git", "--no-optional-locks", "show", "--name-only", "--format=", "--find-renames", sha],
        text=True,
    ).splitlines()
    files = insertions = deletions = 0
    m = re.search(r"(\d+) files? changed", stat)
    if m:
        files = int(m.group(1))
    m = re.search(r"(\d+) insertions?", stat)
    if m:
        insertions = int(m.group(1))
    m = re.search(r"(\d+) deletions?", stat)
    if m:
        deletions = int(m.group(1))
    reasons = []
    if insertions + deletions >= 500:
        reasons.append(f"{insertions + deletions} changed lines")
    if files >= 10:
        reasons.append(f"{files} files")
    if re.search(r"\b(Add|Register|Cover|Scaffold|Invoke|Wire|Expand)\b", subject, re.I):
        reasons.append("artifact/action-shaped subject")
    if any(re.search(r"(^docs?/|README|examples/|tests?/|workflows?|cli|build|pyproject|setup|Makefile)", p) for p in names):
        reasons.append("docs/tests/orchestration/build surface")
    if reasons:
        print(f"{sha[:12]} {subject} :: {'; '.join(reasons)}")
PY
```

Then run the polish in three passes:

1. Final evolution split audit. Inspect every commit in `BASE_SHA..HEAD` in
   order. For each commit, record `keep`, `split`, `reword`, or `integrate` in
   `$DECOMPOSE_STATE_DIR/history-polish-audit.md`, with the concrete reason and
   the smaller product state that should exist after any replacement commit.
   Use the `Split a broad committed snapshot` procedure below for every split
   candidate. Every commit in `history-polish-pressure-list.txt` starts as
   `split` until proven otherwise. A `keep` verdict for one of those commits is
   valid only when the audit lists the concrete split probes considered and the
   exact immediate breakage or narrative regression each probe would cause.
   Also reconcile the subject and body against the patch: every meaningful
   helper, result field, fixture family, REST surface, data model, CLI branch,
   docs section, and build hook must be either the named outcome of the commit,
   required support for that named outcome, or a separate outcome that needs a
   later replacement commit. A narrow subject does not make unmentioned patch
   content part of the same concern.
   Vague explanations such as "single behavior", "same module", "one module",
   "same function", "one function", "one entry point", "single CLI entry
   point", "fixture set", "tests belong together", "tests for one module",
   "tests for one function", "coherent unit", "shared helper", "large but
   related", "single pipeline", "one pipeline", "same pipeline",
   "full pipeline", "execution pipeline", "artificial subdivision",
   "no meaningful subdivision", "across its variants", or "all stages" are
   failed audit entries.

   A pipeline, function, module, command, test file, or fixture tree is not a
   concern boundary by itself. If a pressured `keep` claims that a patch is one
   pipeline, one function, or one module, the audit must name the smallest first
   runnable version of that pipeline/function/module, then list each later
   enrichment, adopter, variant, error path, docs section, fixture, and proof
   that could land after the spine. If any later item can be added while the
   earlier spine still builds and passes its narrow proof, split it. A keep is
   valid only when every proposed later item would immediately break the
   committed snapshot or make the history less coherent in a concrete,
   path-specific way.

   After any rewrite, restart the audit from the beginning because later SHAs
   and dependencies have changed.
   Use this shape for pressured keeps:

   ```text
   #### SHA subject
   - Verdict: KEEP
   - Pressure: 1530 changed lines; tests/orchestration surface
   - Smallest runnable spine: parser setup plus one executor assertion that
     proves the command dispatches a minimal request.
   - Later enrichments checked: fixture builders, second executor mode,
     error-path assertions, docs examples.
   - Split probes considered:
     1. Move parser setup before executor assertions.
        Immediate breakage: test file imports helper X that is introduced by the
        executor assertion block on the same commit; separating would require a
        new smaller helper commit, so create that helper split first or keep is
        invalid.
     2. Move fixture builders before lookaside assertions.
        Immediate breakage: no breakage; split this commit.
   - Result: SPLIT because probe 2 is independently coherent.
   ```

   A pressured `keep` with any "no breakage" probe is not a keep; split it. A
   pressured `keep` that does not name a smallest runnable spine and later
   enrichments is not a keep; continue splitting.
2. Repair/process integration. Scan the full range for commits that restore,
   repair, clean up, compensate for decomposition, or mention process
   mechanics. Use the `Integrate late repair commits` procedure below to amend
   each hunk into the earlier commit where it first belonged, then drop the
   repair/process commit. If a hunk cannot be placed confidently, fail the
   workflow instead of keeping a repair commit.
3. Subject and narrative cleanup. Reword subjects that describe artifacts
   instead of outcomes, contain multiple actions, contain `and`, `also`,
   `as well as`, or a semicolon, or hide multiple behaviors behind a generic
   summary. Reword with an edit stop so the replacement subject can be checked
   against the actual patch:

```bash
BASE_SHA=PUT_BASE_SHA_HERE
BAD_SHA=PUT_COMMIT_WITH_BAD_SUBJECT_HERE
BAD_SHORT=$(git rev-parse --short=7 "$BAD_SHA")
GIT_SEQUENCE_EDITOR="sed -i -E 's/^pick (${BAD_SHORT}[0-9a-f]*) /edit \\1 /'" git rebase -i "$BASE_SHA"
git --no-optional-locks show --stat --patch --find-renames HEAD
NEW_SUBJECT='PUT_SINGLE_OUTCOME_SUBJECT_HERE'
git commit --amend -m "$NEW_SUBJECT"
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/verify-head-snapshot.py --ref HEAD -- python -m compileall -q src tests
git rebase --continue
```

After every split, integration, or reword, rerun the relevant verification
loop over the changed committed snapshots. Do not continue with a failing
intermediate commit just because the final tree will be fixed later.

Before reporting success, reject weak audit language, rerun Gate 3 in full,
and verify that the final tree did not change:

```bash
python - <<'PY'
import os
import re
import sys
from pathlib import Path

audit = Path(os.environ["DECOMPOSE_STATE_DIR"]) / "history-polish-audit.md"
text = audit.read_text(encoding="utf-8")
weak = re.compile(
    r"\b(single behavior|same module|one module|same function|one function|"
    r"one entry point|single CLI entry point|fixture set|tests belong together|"
    r"tests for one module|tests for one function|coherent unit|shared helper|"
    r"large but related|single pipeline|one pipeline|same pipeline|"
    r"full pipeline|execution pipeline|artificial subdivision|"
    r"no meaningful subdivision|across its variants|all stages)\b",
    re.I,
)
matches = [line for line in text.splitlines() if weak.search(line)]
if matches:
    print("history-polish audit has weak keep rationale; split or write concrete immediate breakage")
    print("\n".join(matches[:20]))
    sys.exit(1)
blocks = re.split(r"\n####\s+", text)
missing_spine = []
for block in blocks:
    if "Verdict: KEEP" not in block or "Pressure:" not in block:
        continue
    if not re.search(r"\b(pipeline|function|module|command|entry point|test file|fixture tree)\b", block, re.I):
        continue
    if not re.search(r"Smallest runnable", block, re.I):
        missing_spine.append(block.splitlines()[0][:160])
if missing_spine:
    print("pressured keep lacks Smallest runnable spine analysis:")
    print("\n".join(missing_spine[:20]))
    sys.exit(1)
PY
test "$(git --no-optional-locks rev-parse HEAD^{tree})" = "$(cat "$DECOMPOSE_STATE_DIR/history-polish-pre-tree.txt")"
git --no-optional-locks rev-list --count "$BASE_SHA"..HEAD > "$DECOMPOSE_STATE_DIR/history-polish-post-count.txt"
git --no-optional-locks log --reverse --format='%H %s' "$BASE_SHA"..HEAD > "$DECOMPOSE_STATE_DIR/history-polish-post-series.txt"
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase history-polish-complete --note "history polish passed"
```

The completion report for this mode must include the original commit count,
the final commit count, which commits were split, which repair/process commits
were integrated or dropped, which subjects were reworded, which pressured
commits were kept with exact breakage reasons, and the validation commands that
passed.

## Operating Contract

These rules override any conflicting behavior. They apply to all phases.

- A concern is a product, workflow, or architectural capability — not an
  artifact category.
- The primary deliverable is a believable evolving history where each commit
  reads like the next step a maintainer could have taken.
- Before choosing concern boundaries, build a simplified-project evolution
  ladder. Each ladder step names the smaller product that would exist after
  that step, the regions/tests that prove it, and the future content that
  must not appear yet. Concerns and rebuild commits must trace back to this
  ladder.
- After drafting concerns, run a concern refinement pass before Gate 1.
  Inspect every concern as if it were an incoming batch. Expand its
  `expected_commits`, `internal_slices`, `files_wholly_owned`, and shared
  regions into plausible smaller concerns. If any smaller concern would be
  coherent, promote it into the concern list before batching. Do not leave
  independently useful behaviors hidden inside `expected_commits` or
  `internal_slices`.
- Write transient decomposition artifacts under the workspace-local workflow
  state directory printed by `decompose-checkpoint.py state-dir`. The default
  is `$REPO_ROOT/.git-stage-batch/`, not `.git`, `.claude`, or `/var/tmp`.
  At the start of every run, create that directory and run
  `git-stage-batch block-file --local-only .git-stage-batch/` before writing
  state. Override with `DECOMPOSE_STATE_DIR` only when needed.
- Before writing JSON, write `decompose-narrative.md` in that workflow state
  directory.
  The narrative must describe the current committed `HEAD` in detail, the
  final working tree in detail, and how each module, command, test file, docs
  section, build file, or fixture tree that already exists at `HEAD` evolves.
  For each new file, it must describe the smallest first version and later
  growth.
- The narrative must describe changes, not only additions. For every
  existing committed code path, command, test, docs section, build file, or
  fixture surface that is touched, say how the existing thing changes at each
  relevant step and which final-tree content is still absent.
- Every intermediate `HEAD` must be coherent. Do not create a commit that
  knowingly leaves an import, parser, registry, submodule, or tested entry
  point broken for a later commit to repair.
- An adopter cannot land before the behavior it adopts. CLI handlers, parser
  entries, docs sections, examples, and coordinators must not rely on
  call-time imports, untested branches, placeholders, or future providers to
  be coherent. High-level user workflows land after the lower operations they
  invoke.
- Final module-level imports are not hard dependency constraints. Imports,
  `__all__` entries, parser registrations, dispatch tables, registry rows,
  and docs indexes must evolve with the concern that first uses each symbol or
  entry. Do not force a provider to land early merely because the final file
  imports it at top level.
- Shared infrastructure is not a keep-together reason. It is the reason to
  create an earlier scaffold concern, followed by one consumer, provider,
  workflow, command branch, or result shape at a time.
- Do not create broad foundation/core/infrastructure buckets. Independent
  no-import utilities, model records, enforcement hooks, replay paths, and
  test groups still land one first-consumer behavior at a time.
- Every concern batch must also be coherent as a replayable unit. During
  deconstruction, validate the batch ref itself before moving on; a remaining
  working tree can be repaired while the batch still contains a partial parser
  call, partial test, or other unrebuildable slice.
- Distinguish ownership from shared syntax context. Use `discard --to` for
  the current concern's owned lines. Use `include --to` to copy shared
  wrappers, neighboring entries, or aggregation context needed for the batch
  to parse; copied context does not become part of the concern.
- Commit subjects must name one action in one clause. If the subject
  contains `and`, `also`, `as well as`, a semicolon, or two independent
  actions, the commit must be split.
- A generic subject is not a loophole. If expanding the staged diff into
  concrete items reveals multiple independently useful behaviors, split the
  commit even when the subject itself is short and grammatical.
- A complete file, module, test suite, coordinator, or docs section is not a
  concern by default. If it looks like a finished subsystem copied from the
  final tree, split it into the ladder steps by which the subsystem could
  have grown.
- A pipeline, function, module, command, test file, or fixture tree is not a
  concern by default. First commit the smallest runnable spine that has a
  coherent product outcome and narrow proof. Later enrichments, variants,
  adopters, error paths, docs sections, fixtures, and tests land as subsequent
  commits unless moving them later would immediately break a committed
  snapshot in a concrete, path-specific way.
- Do not mention decomposition, batches, repairs, peeling, reconstruction,
  or process mechanics in commit messages.
- Scale is not a split criterion. Hundreds of tool calls means the work is
  large, not that the split should be broadened.
- Pragmatic shortcuts are false economy. "Good enough for now", broad
  grouping to save time, shared-region-only ownership, deferred tests, future
  repair commits, and vague summaries will fail the gates and force the work
  to be repeated with more context spent. Get the concern boundary, batch
  boundary, and commit boundary right the first time.
- `git-stage-batch apply --from BATCH` restores batch content to the
  working tree only. It does not stage anything. During rebuild, treat the
  restored content as an unstaged diff, then stage commit-sized slices with
  the same care used by `commit-unstaged-changes`.
- The staging primitive for this workflow is `git-stage-batch include`.
  Do not use Git's native interactive, partial, path, or whole-tree staging
  to assemble commit slices. Plain `git add` is reserved only for marking
  manually resolved rebase conflicts, not for ordinary commit construction.
  When the right historical snapshot is clearer as a rewritten file than as
  selected original hunks, stage that intentional snapshot with
  `git-stage-batch include --file PATH --as-stdin`; it is still a
  `git-stage-batch include` operation and must be audited as one atomic
  commit.
- Before choosing `git-stage-batch` syntax, re-check the relevant installed
  subcommand help. Never use a guessed `git-stage-batch` command, flag, or
  selector; the command must be present in the help/man page you just read.
- Do not describe `apply --from` as applying to the index. If a batch should
  be staged directly, the command family is `include --from`; do not use that
  shortcut during this workflow unless there is a deliberate reason and the
  resulting staged diff is still audited as one atomic commit.
- A fresh end-to-end run must not reuse old `decompose-*` batches or an old
  `git-stage-batch` session. Stale batches are inputs from another attempt,
  not evidence for the current decomposition.

## Git Command Concurrency

Always pass `--no-optional-locks` to read-only git commands (`status`,
`diff`, `log`, `show`, `ls-tree`). Without it, parallel git commands race
for `.git/index.lock`.

## Phase 1: Analysis

Record the base commit for later reference:

```bash
BASE_SHA=$(git --no-optional-locks log --format='%H' -1)
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py start --mode full --base "$BASE_SHA"
```

For a `deconstruct` run, use `--mode deconstruct`. For a `resume` run that
reruns Phase 1, use `--mode resume` and keep the original checkpoint `base`
if one exists.

Spawn `Agent(decompose-analyzer)` with this prompt:

> Analyze the unstaged working tree and produce a structured concern plan.
> Compute `DECOMPOSE_STATE_DIR` with
> `python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py state-dir`.
> First write `$DECOMPOSE_STATE_DIR/decompose-narrative.md`: a prose
> evolution narrative from the current committed state to the final working
> tree. Describe the current committed state in detail, the final working tree
> in detail, the beginning/middle/end of the history, how each module,
> command, test file, docs section, build file, or fixture tree that already
> exists at HEAD evolves, including a required Existing Surface Evolution
> table for every touched committed path, and the smallest first version of
> each new file in a required New Surface Growth table. Treat final
> module-level imports, registries, parser tables, dispatch maps, and docs
> indexes as evolving surfaces, not fixed constraints from the final tree;
> describe them in a required Aggregation Evolution table.
> Then write a simplified-project evolution ladder. Derive concern boundaries
> from that narrative and ladder instead of from final file/module boundaries.
> Run a concern refinement pass before writing the final candidate: for every
> concern, review its expected commits, internal slices, whole-file claims,
> shared regions, docs, tests, fixtures, and CLI/build/parser changes as
> possible sub-concerns. Split any independently coherent behavior into a new
> concern. Write `$DECOMPOSE_STATE_DIR/decompose-refinement.md` with the
> before/after concern list and the exact immediate breakage for every
> retained keep-together decision.
> Do not read any existing `$DECOMPOSE_STATE_DIR/decompose-plan.json` as
> input. Write only `$DECOMPOSE_STATE_DIR/decompose-plan.candidate.json`;
> replace that candidate path if it already exists.
> After writing the narrative and candidate plan, run
> `python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase1-candidate --note "candidate plan written"`.
> The current base commit is {BASE_SHA}.

### Concern Refinement Pass

Before Gate 1 promotion, the candidate must pass a refinement pass. This pass
prevents broad batches from surviving by hiding smaller behaviors inside
`expected_commits`, `internal_slices`, or whole-file ownership.

Read `$DECOMPOSE_STATE_DIR/decompose-plan.candidate.json` and
`$DECOMPOSE_STATE_DIR/decompose-narrative.md`, then verify every concern:

1. Expand each `expected_commits` entry into a candidate sub-concern. If two
   entries would each leave a coherent product state, split the concern.
2. Expand every `internal_slices` entry into a candidate sub-concern. If a
   slice has its own proving tests, user-visible result, parser branch,
   provider variant, fixture path, data record, or docs section, split it.
3. Treat `files_wholly_owned` as suspicious, not reassuring. Large source,
   test, docs, coordinator, fixture, and orchestration files must evolve
   through concerns unless generated or data-only.
4. For each shared file region, ask whether the region is owned behavior or
   only syntax context. Owned behavior becomes a concern; context stays
   `include`-only.
5. Rewrite the candidate so the concern list, peel order, rebuild order,
   evolution ladder, dependencies, and narrative milestones reflect the
   refined concerns.
6. Write `$DECOMPOSE_STATE_DIR/decompose-refinement.md` with one section per
   original concern: original purpose, proposed sub-concerns, promoted
   sub-concerns, retained keep-together decisions, and exact immediate
   breakage for each retained decision.

`internal_slices` are allowed only as a scalpel map for one retained concern
that truly cannot be made coherent as smaller concerns. They are not a
substitute for concern splitting.

### Gate 1: Validate the concern plan

After Phase 1 completes, read `$DECOMPOSE_STATE_DIR/decompose-narrative.md`
and `$DECOMPOSE_STATE_DIR/decompose-plan.candidate.json`, then run this
mechanical validator before doing any subjective review:

Run this validator exactly as written against the candidate path. Do not
substitute an older validator, a shorter validator, or validation against
`$DECOMPOSE_STATE_DIR/decompose-plan.json`.

```bash
python - <<'PY'
import json, re, subprocess, sys
from pathlib import Path
state_dir = Path(subprocess.check_output(
    ["python", ".claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py", "state-dir"],
    text=True,
).strip())
narrative_path = state_dir / "decompose-narrative.md"
if not narrative_path.exists():
    print(f"- missing {narrative_path}", file=sys.stderr)
    sys.exit(1)
narrative = narrative_path.read_text(encoding="utf-8")
refinement_path = state_dir / "decompose-refinement.md"
if not refinement_path.exists():
    print(f"- missing {refinement_path}", file=sys.stderr)
    sys.exit(1)
refinement = refinement_path.read_text(encoding="utf-8")
if len(refinement.strip()) < 200 or not re.search(r"\b(promoted sub-concerns|retained keep-together|expected_commits|internal_slices)\b", refinement, re.I):
    print(f"- {refinement_path} does not look like a real concern refinement audit", file=sys.stderr)
    sys.exit(1)
required_sections = [
    "Current Committed State",
    "Final Working Tree State",
    "Existing Surface Evolution",
    "New Surface Growth",
    "Aggregation Evolution",
    "Beginning",
    "Middle",
    "End",
    "Forbidden Shortcuts Found",
]
narrative_bad = [
    section for section in required_sections
    if not re.search(rf"^#+\s+{re.escape(section)}\s*$", narrative, re.M)
]
if narrative_bad:
    print(
        "\n".join(f"- narrative missing section: {section}" for section in narrative_bad),
        file=sys.stderr,
    )
    sys.exit(1)

def section_body(text, heading):
    match = re.search(rf"^#+\s+{re.escape(heading)}\s*$", text, re.M)
    if match is None:
        return ""
    rest = text[match.end():]
    next_heading = re.search(r"^#+\s+\S.*$", rest, re.M)
    return rest[: next_heading.start()] if next_heading else rest

existing_surface_section = section_body(narrative, "Existing Surface Evolution")
new_surface_section = section_body(narrative, "New Surface Growth")
aggregation_section = section_body(narrative, "Aggregation Evolution")
middle_section = section_body(narrative, "Middle")
plan = json.load(open(state_dir / "decompose-plan.candidate.json", encoding="utf-8"))
plan_text = json.dumps(plan, sort_keys=True)
bad = []
try:
    modified_existing_paths = subprocess.check_output(
        ["git", "--no-optional-locks", "diff", "--name-only", "--diff-filter=M", "HEAD"],
        text=True,
    ).splitlines()
    added_paths = subprocess.check_output(
        ["git", "--no-optional-locks", "diff", "--name-only", "--diff-filter=A", "HEAD"],
        text=True,
    ).splitlines()
    untracked_paths = subprocess.check_output(
        ["git", "--no-optional-locks", "ls-files", "--others", "--exclude-standard", "--directory"],
        text=True,
    ).splitlines()
    added_paths = sorted(set(added_paths + untracked_paths))
except subprocess.CalledProcessError as exc:
    bad.append(f"failed to inspect working tree paths: {exc}")
    modified_existing_paths = []
    added_paths = []

large_new_code_paths = []
for path in added_paths:
    path_obj = Path(path)
    if path_obj.exists() and path_obj.is_file() and path.endswith((".py", ".pyi")):
        try:
            line_count = len(path_obj.read_text(encoding="utf-8", errors="ignore").splitlines())
        except OSError:
            line_count = 0
        if line_count > 600:
            large_new_code_paths.append((path, line_count))

def is_new_surface_path(path):
    if path.startswith(".git/"):
        return False
    if path == ".gitmodules":
        return True
    if path.endswith("/"):
        return path.startswith(("examples/", "docs/", "src/", "tests/"))
    return path.endswith((".py", ".md", ".toml", ".json", ".yaml", ".yml"))

for path in modified_existing_paths:
    if path not in existing_surface_section:
        bad.append(f"Existing Surface Evolution missing modified HEAD path: {path}")
for path in added_paths:
    if path not in new_surface_section and is_new_surface_path(path):
        bad.append(f"New Surface Growth missing added surface: {path}")
module_catalog_headings = re.findall(
    r"^#{3,}\s+.*\.(?:py|md|toml|json|ya?ml)\b",
    middle_section,
    re.M,
)
if len(module_catalog_headings) >= 5:
    bad.append("Middle looks like a file/module catalog; make Middle historical-step prose")
for paragraph in re.split(r"\n\s*\n", middle_section):
    file_mentions = re.findall(r"\b[\w./-]+\.(?:py|md|toml|json|ya?ml)\b", paragraph)
    if len(set(file_mentions)) >= 5:
        bad.append("Middle paragraph bundles too many files; split it into behavior steps")
    variants = set(re.findall(r"\b(triage|backport|rebase|rebuild)\b", paragraph, re.I))
    if len(variants) > 1:
        bad.append("Middle paragraph bundles multiple workflow variants")
if re.search(
    r"\b(call[- ]time imports?|imported at call time|lazy imports?|placeholder|"
    r"untested branches?|future providers?|primary user workflow)\b",
    narrative + "\n" + plan_text,
    re.I,
):
    bad.append("Plan uses call-time imports, placeholders, untested branches, future providers, or primary-workflow priority as a coherence excuse")
if re.search(r"\b(foundation|core|infrastructure)\s+(layer|bucket)\b", middle_section, re.I):
    bad.append("Middle uses a broad foundation/core/infrastructure bucket")
if modified_existing_paths and "|" not in existing_surface_section:
    bad.append("Existing Surface Evolution must use a table with step, before, change, and absent columns")
if added_paths and "|" not in new_surface_section:
    bad.append("New Surface Growth must use a table with step, smallest version, first consumer/test, and absent columns")
if "|" not in aggregation_section:
    bad.append("Aggregation Evolution must use a table for imports, parser registrations, registries, dispatch maps, and docs indexes")
raw_concerns = plan.get("concerns", [])
if not isinstance(raw_concerns, list):
    bad.append("concerns must be a list")
    raw_concerns = []
raw_ladder = plan.get("evolution_ladder", [])
if not isinstance(raw_ladder, list) or not raw_ladder:
    bad.append("evolution_ladder must be a non-empty list")
    raw_ladder = []
ladder_steps = []
ladder_region_paths = set()
modified_ladder_paths = set()
for i, step in enumerate(raw_ladder, 1):
    if not isinstance(step, dict):
        bad.append(f"evolution_ladder entry {i} must be an object")
        continue
    step_no = step.get("step")
    if not isinstance(step_no, int):
        bad.append(f"evolution_ladder entry {i}: step must be an integer")
    else:
        ladder_steps.append(step_no)
    for key in ("behavior_after", "why_next_simplest"):
        if not isinstance(step.get(key), str) or not step.get(key, "").strip():
            bad.append(f"evolution_ladder entry {i}: missing {key}")
    for key in ("regions_introduced_or_evolved", "tests", "must_not_appear_yet"):
        value = step.get(key)
        if not isinstance(value, list) or not value:
            bad.append(f"evolution_ladder entry {i}: {key} must be a non-empty list")
    regions = step.get("regions_introduced_or_evolved")
    if isinstance(regions, list):
        for j, region in enumerate(regions, 1):
            if not isinstance(region, dict):
                bad.append(f"evolution_ladder entry {i}: region {j} must be an object")
                continue
            for key in ("path", "anchor", "change_kind", "before_state", "after_state", "still_absent"):
                if key == "still_absent":
                    if not isinstance(region.get(key), list):
                        bad.append(f"evolution_ladder entry {i}: region {j} still_absent must be a list")
                elif not isinstance(region.get(key), str) or not region.get(key, "").strip():
                    bad.append(f"evolution_ladder entry {i}: region {j} missing {key}")
            path = region.get("path")
            if isinstance(path, str):
                ladder_region_paths.add(path)
            change_kind = region.get("change_kind")
            if change_kind not in {"introduced", "modified-from-head", "modified-from-earlier"}:
                bad.append(f"evolution_ladder entry {i}: region {j} invalid change_kind")
            elif change_kind.startswith("modified") and isinstance(path, str):
                modified_ladder_paths.add(path)
if ladder_steps and sorted(ladder_steps) != list(range(1, len(ladder_steps) + 1)):
    bad.append("evolution_ladder steps must be unique and contiguous from 1")
for path in modified_existing_paths:
    if path not in modified_ladder_paths:
        bad.append(f"evolution_ladder does not describe modified existing path: {path}")
ladder_set = set(ladder_steps)
concerns = []
for i, concern in enumerate(raw_concerns, 1):
    if isinstance(concern, dict):
        concerns.append(concern)
    else:
        bad.append(f"concern entry {i} must be an object")
numbers = [c.get("number") for c in concerns]
if any(not isinstance(n, int) for n in numbers) or sorted(numbers) != list(range(1, len(numbers) + 1)):
    bad.append("concern numbers must be unique and contiguous from 1")
number_set = set(n for n in numbers if isinstance(n, int))
expected_order = list(range(1, len(concerns) + 1))
if plan.get("peel_order") != expected_order:
    bad.append("peel_order must be [1..N] in outermost-to-innermost order")
if plan.get("rebuild_order") != list(reversed(expected_order)):
    bad.append("rebuild_order must be the exact reverse of peel_order")
risky_whole_files = {
    "cli.py", "README.md", "pyproject.toml", "hatch_build.py",
    "ymir_workflows.py", "models.py", "validation.py", "collect_case.py",
    "capture_missing.py", "runner.py", "conftest.py",
    "test_cli.py", "test_collect_case.py", "test_capture_missing.py",
    "test_runner.py", "test_ymir_workflows.py",
}
invalid_keep_together = re.compile(
    r"\b(shared|common|duplicate|duplication|same file|same module|same function|"
    r"unused import|ruff|F401|live together|used together|helper|helpers|"
    r"infrastructure)\b",
    re.I,
)
proofish_commit = re.compile(
    r"\b(test|tests|proof|prove|cover|coverage|assert|reject|accept|pin|"
    r"regression|document|docs?)\b",
    re.I,
)
implementation_commit = re.compile(
    r"\b(add|implement|wire|register|scaffold|fetch|record|capture|collect|"
    r"compare|score|validate|run|execute|render|manage|patch|materialize|"
    r"parse|load|write|resolve|normalize|derive|create|enable|support|handle)\b",
    re.I,
)
for c in concerns:
    label = c.get("name") or c.get("slug") or f"#{c.get('number')}"
    purpose = c.get("purpose", "")
    if not isinstance(purpose, str):
        bad.append(f"{label}: purpose must be a string")
        purpose = str(purpose)
    number = c.get("number")
    if not isinstance(number, int):
        bad.append(f"{label}: number must be an integer")
    evolution_step = c.get("evolution_step")
    if not isinstance(evolution_step, int):
        bad.append(f"{label}: evolution_step must be an integer")
    elif ladder_set and evolution_step not in ladder_set:
        bad.append(f"{label}: evolution_step {evolution_step} is not in evolution_ladder")
    milestone = c.get("narrative_milestone")
    if not isinstance(milestone, str) or not milestone.strip():
        bad.append(f"{label}: missing narrative_milestone")
    if re.search(r"\b(and|also)\b|as well as|;|,", purpose, re.I):
        bad.append(f"{label}: purpose uses a forbidden conjunction or comma list")
    slug = c.get("slug", "")
    if not isinstance(slug, str):
        bad.append(f"{label}: slug must be a string")
        slug = str(slug)
    if re.search(r"\b(all|shared|common|mixed|integration|documentation|tests|fixtures|cli-wiring)\b", slug, re.I):
        bad.append(f"{label}: slug is an artifact or umbrella category")
    deps = c.get("depends_on", [])
    if not isinstance(deps, list):
        bad.append(f"{label}: depends_on must be a list of integer concern numbers")
        deps = []
    for dep in deps:
        if not isinstance(dep, int):
            bad.append(f"{label}: depends_on entries must be integer concern numbers")
        elif dep not in number_set:
            bad.append(f"{label}: depends_on references unknown concern {dep}")
        elif isinstance(number, int) and dep <= number:
            bad.append(f"{label}: depends_on must point inward to a higher-numbered concern")
    ops = c.get("externally_invocable_operations", [])
    if not isinstance(ops, list):
        bad.append(f"{label}: externally_invocable_operations must be a list")
        ops = []
    if len(ops) > 1:
        bad.append(f"{label}: multiple externally invocable operations must be split")
    for op in ops:
        if not isinstance(op, str):
            bad.append(f"{label}: externally_invocable_operations entries must be strings")
            continue
        if "|" in op or re.search(r",\s*\S", op):
            bad.append(f"{label}: operation string hides variants: {op}")
    falsification = c.get("falsification", {})
    if not isinstance(falsification, dict):
        bad.append(f"{label}: falsification must be an object")
        falsification = {}
    files_wholly_owned = c.get("files_wholly_owned", [])
    if not isinstance(files_wholly_owned, list):
        bad.append(f"{label}: files_wholly_owned must be a list")
        files_wholly_owned = []
    internal_slices = c.get("internal_slices", [])
    if internal_slices is not None and not isinstance(internal_slices, list):
        bad.append(f"{label}: internal_slices must be a list when present")
        internal_slices = []
    refinement_audit = c.get("refinement_audit", {})
    if not isinstance(refinement_audit, dict):
        bad.append(f"{label}: missing refinement_audit object from concern refinement pass")
        refinement_audit = {}
    independent_count = refinement_audit.get("independent_behavior_count")
    if not isinstance(independent_count, int) or independent_count < 1:
        bad.append(f"{label}: refinement_audit.independent_behavior_count must be a positive integer")
    elif independent_count > 1:
        bad.append(f"{label}: refinement pass found {independent_count} independent behaviors; split the concern")
    promoted = refinement_audit.get("promoted_subconcerns", [])
    if promoted:
        bad.append(f"{label}: promoted_subconcerns is non-empty; rewrite candidate with those sub-concerns promoted")
    reviewed_slices = refinement_audit.get("reviewed_internal_slices", [])
    if internal_slices and not isinstance(reviewed_slices, list):
        bad.append(f"{label}: refinement_audit.reviewed_internal_slices must review every internal slice")
    elif len(internal_slices) > 1 and len(reviewed_slices) != len(internal_slices):
        bad.append(f"{label}: refinement pass did not review every internal_slices entry")
    if len(internal_slices) > 1 and not refinement_audit.get("why_slices_are_not_concerns"):
        bad.append(f"{label}: multiple internal_slices require why_slices_are_not_concerns")
    for path in files_wholly_owned:
        if not isinstance(path, str):
            bad.append(f"{label}: files_wholly_owned entries must be strings")
            continue
        basename = path.rsplit("/", 1)[-1]
        if basename in risky_whole_files and not internal_slices:
            bad.append(f"{label}: risky whole file needs narrower concerns: {path}")
        path_obj = Path(path)
        if (
            path_obj.exists()
            and path_obj.is_file()
            and path.endswith((".py", ".pyi"))
            and not re.search(r"\b(generated|fixture|data-only)\b", json.dumps(c), re.I)
        ):
            try:
                line_count = len(path_obj.read_text(encoding="utf-8", errors="ignore").splitlines())
            except OSError:
                line_count = 0
            if line_count > 600:
                bad.append(f"{label}: large code/test file cannot remain files_wholly_owned; split {path} across refined concerns")
    split_audit = c.get("split_audit", {})
    if not isinstance(split_audit, dict):
        bad.append(f"{label}: split_audit must be an object")
        split_audit = {}
    splits = split_audit.get("candidate_splits", [])
    if not isinstance(splits, list):
        bad.append(f"{label}: split_audit.candidate_splits must be a list")
        splits = []
    if len(splits) == 0:
        bad.append(f"{label}: missing candidate split audit")
    for split in splits:
        if not isinstance(split, dict):
            bad.append(f"{label}: split_audit entries must be objects with proposal, breakage, verdict")
            continue
        for key in ("proposal", "breakage", "verdict"):
            if not split.get(key):
                bad.append(f"{label}: split_audit entry missing {key}")
        breakage_text = " ".join(str(split.get(k, "")) for k in ("proposal", "breakage"))
        if split.get("verdict") == "keep-together" and invalid_keep_together.search(breakage_text):
            bad.append(f"{label}: invalid keep-together excuse: {breakage_text}")
        if split.get("verdict") == "keep-together" and re.search(r"\b(N/A|none|not applicable|later|together)\b", str(split.get("breakage", "")), re.I):
            bad.append(f"{label}: keep-together split audit lacks exact immediate breakage")
    if falsification.get("verdict") == "keep-together" and re.search(
        r"\b(N/A|none|not applicable|later|together)\b",
        " ".join(str(falsification.get(k, "")) for k in ("narrower_split", "breakage")),
        re.I,
    ):
        bad.append(f"{label}: keep-together falsification lacks exact immediate breakage")
    falsification_text = " ".join(str(falsification.get(k, "")) for k in ("narrower_split", "breakage"))
    if falsification.get("verdict") == "keep-together" and invalid_keep_together.search(falsification_text):
        bad.append(f"{label}: invalid keep-together falsification excuse: {falsification_text}")
    regions = c.get("shared_file_regions", [])
    if not isinstance(regions, list):
        bad.append(f"{label}: shared_file_regions must be a list")
        regions = []
    for region in regions:
        if not isinstance(region, dict):
            bad.append(f"{label}: shared_file_regions entries must be objects")
            continue
        path = region.get("path", "")
        basename = path.rsplit("/", 1)[-1]
        anchor = f"{region.get('anchor', '')} {region.get('description', '')}"
        is_risky = basename in risky_whole_files or path.startswith(("tests/", ".github/", "docs/"))
        if is_risky and re.search(r"\bwhole file\b|\ball of\b|\bentire file\b", anchor, re.I):
            bad.append(f"{label}: shared file region claims a whole risky file: {path}")
        for start, end in re.findall(r"lines?\s+(\d+)\s*-\s*(\d+)", anchor, re.I):
            if is_risky and int(end) - int(start) + 1 > 180:
                bad.append(f"{label}: shared file region is too large in {path}: lines {start}-{end}")
    expected_commits = c.get("expected_commits", [])
    if not isinstance(expected_commits, list):
        bad.append(f"{label}: expected_commits must be a list")
        expected_commits = []
    reviewed_commits = refinement_audit.get("reviewed_expected_commits", [])
    if len(expected_commits) > 1:
        if not isinstance(reviewed_commits, list) or len(reviewed_commits) != len(expected_commits):
            bad.append(f"{label}: refinement pass must review every expected_commits entry as a possible sub-concern")
        non_proof_commits = [
            str(commit) for commit in expected_commits
            if implementation_commit.search(str(commit)) and not proofish_commit.search(str(commit))
        ]
        if len(non_proof_commits) > 1:
            bad.append(
                f"{label}: expected_commits contain multiple implementation/adopter slices; "
                f"promote them to concerns: {non_proof_commits}"
            )
    if len(expected_commits) == 2:
        joined = " ".join(map(str, expected_commits))
        if re.search(r"\b(implement|implementation|code)\b", joined, re.I) and re.search(r"\b(tests?|cover)\b", joined, re.I):
            bad.append(f"{label}: expected commits look like implementation-plus-tests instead of behavior slices")
    if len(expected_commits) >= 4:
        testish = [
            bool(re.search(r"\b(tests?|test|proof|prove|cover|coverage|fixture|assert|rejects?|accepts?)\b", str(commit), re.I))
            for commit in expected_commits
        ]
        if any(testish) and not all(testish):
            first_test = testish.index(True)
            if first_test >= 2 and all(testish[first_test:]) and len(testish) - first_test >= 2:
                bad.append(f"{label}: expected commits group implementation before tests; interleave each behavior slice with its proof")
    for text in [purpose, " ".join(map(str, expected_commits))]:
        if re.search(r"\ball\b|\bcomplete\b|\bentire\b|\bfull\b|\bshared\b|\bcommon\b|\bmixed\b|\bintegration\b", text, re.I):
            bad.append(f"{label}: broad wording suggests a dump")
    expected_joined = " ".join(map(str, expected_commits))
    if "|" in expected_joined:
        bad.append(f"{label}: expected commits hide variants with |")
    if len(re.findall(r"\b(triage|backport|rebase|rebuild)\b", expected_joined, re.I)) > 1:
        bad.append(f"{label}: expected commits mention multiple workflow variants")
for path, line_count in large_new_code_paths:
    plan_mentions = []
    owning_mentions = []
    path_specific_slices = 0
    generated_or_data_only = False
    for c in concerns:
        label = c.get("name") or c.get("slug") or f"#{c.get('number')}"
        ctext = json.dumps(c)
        if path not in ctext:
            continue
        plan_mentions.append(str(label))
        if path in c.get("files_wholly_owned", []):
            owning_mentions.append(str(label))
        if re.search(r"\b(generated|data-only)\b", ctext, re.I):
            generated_or_data_only = True
        slices = c.get("internal_slices") or []
        if isinstance(slices, list):
            path_specific_slices += sum(1 for item in slices if path in json.dumps(item))
    if generated_or_data_only:
        continue
    if not plan_mentions:
        bad.append(f"large new code/test file missing from concern plan: {path} has {line_count} lines")
    elif owning_mentions:
        bad.append(f"large new code/test file is wholly owned by a concern; split into refined concerns: {path} owners={owning_mentions}")
    elif len(set(plan_mentions)) < 2:
        bad.append(f"large new code/test file must evolve across multiple concerns, not only internal_slices: {path} mentions={plan_mentions}")
    elif path_specific_slices < 2:
        bad.append(
            f"large new code/test file needs multiple path-specific internal_slices: "
            f"{path} has {line_count} lines, mentions={plan_mentions}, slices={path_specific_slices}"
        )
if bad:
    print("\n".join(f"- {item}" for item in bad), file=sys.stderr)
    sys.exit(1)
PY
```

If the validator fails, re-run Phase 1 with the exact failures. Do not edit
the JSON in place to silence the failure. A Gate 1 failure means the concern
boundaries, schema, order, or falsification evidence are wrong. It is not a
copy-editing task.

In particular, never replace `and` with a comma to make a purpose look
single-clause. Commas in purposes are also forbidden because they usually hide
the same multi-capability concern. Split the concern or rework the dependency
order instead.

Then check:

1. Every concern has a unique, contiguous number.
2. Every concern name is a kebab-case capability slug — not an artifact
   category (`cli-wiring`, `readme`, `docs`, `tests`, `shared`, `mixed`).
3. Every concern purpose is one clause with no `and`, `also`, `as well as`,
   semicolon, comma-separated capability list, or vague umbrella noun.
4. Every `depends_on` entry is an integer concern number, and because
   concerns are numbered outermost-to-innermost, every dependency points to a
   higher-numbered concern.
5. No concern contains multiple externally invocable operations; split
   external commands, workflows, providers, replay paths, and capture paths.
6. Submodule entries exist for every `.gitmodules` path, with gitlink
   ownership assigned.
7. Every concern has `externally_invocable_operations` and an object-shaped
   `split_audit` with exact breakage for every keep-together verdict.
8. The peel order is `[1..N]` and rebuild order is the exact reverse.
9. The plan has a non-empty `evolution_ladder`, and every concern names the
   ladder step it implements through `evolution_step`.
10. Expected commits do not hide sub-concerns. If multiple expected commits
   are independently useful implementation, adopter, docs, fixture, provider,
   parser, or build-system slices, split them into sibling concerns before
   Gate 1. Proof should land with or immediately after the behavior it
   proves.
11. `$DECOMPOSE_STATE_DIR/decompose-narrative.md` exists and includes
    Current Committed State, Final Working Tree State, Existing Surface
    Evolution, New Surface Growth, Aggregation Evolution, Beginning, Middle,
    End, and Forbidden Shortcuts Found.
12. `$DECOMPOSE_STATE_DIR/decompose-refinement.md` exists, and every concern
    has `refinement_audit` proving the refinement pass reviewed
    `expected_commits`, `internal_slices`, and whole-file claims as possible
    sub-concerns.
13. Every concern has `narrative_milestone`, at most one externally
    invocable operation, and no operation string hiding variants with `|`.
14. No keep-together explanation relies on shared helpers, duplicate
    infrastructure, same file/module/function, unused imports, or linter
    failures.
15. The narrative explains how existing committed code and documentation
    changes across the history, not only which new files are added.
16. The plan evolves imports, registries, dispatch tables, parser
    registrations, and docs indexes with their first consumers instead of
    treating final top-level imports as hard ordering constraints.
17. Every `evolution_ladder.regions_introduced_or_evolved` entry is an
    object with path, anchor, change kind, before state, after state, and
    still-absent future content.
18. New untracked code, docs, config, build, and fixture surfaces from
    `git ls-files --others --exclude-standard --directory` are covered by
    New Surface Growth.
19. No adopter, docs section, parser entry, or coordinator lands before the
    behavior it invokes or describes by relying on call-time imports,
    placeholders, untested branches, or future providers.
20. Any new code or test file over 600 lines is split across multiple refined
    concerns unless it is generated or data-only. Listing the file under
    `shared_file_regions` or `internal_slices` is not enough; the plan must
    make the file grow through concern boundaries.

If any check fails, report the failure and re-run Phase 1 with the specific
feedback. Do not proceed to Phase 2 with a failing plan.

After Gate 1 passes, promote the candidate to the final plan path:

```bash
python - <<'PY'
import subprocess
from pathlib import Path
state_dir = Path(subprocess.check_output(
    ["python", ".claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py", "state-dir"],
    text=True,
).strip())
state_dir.mkdir(parents=True, exist_ok=True)
src = state_dir / "decompose-plan.candidate.json"
dst = state_dir / "decompose-plan.json"
dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
PY
```

Then checkpoint the passed analysis:

```bash
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase1-complete --note "Gate 1 passed"
```

## Phase 2: Deconstruction

Spawn `Agent(decompose-deconstructor)` with this prompt:

> Execute the deconstruction phase using the concern plan at
> `$DECOMPOSE_STATE_DIR/decompose-plan.json` and the evolution narrative at
> `$DECOMPOSE_STATE_DIR/decompose-narrative.md`. Compute `DECOMPOSE_STATE_DIR`
> with the checkpoint helper if it is not already set. Peel concerns from
> outermost to innermost.
> Before starting, run
> `python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-running`.
> For every concern, read its `evolution_step` and the matching
> `evolution_ladder` entry, plus its `narrative_milestone`, before selecting
> lines. The batch should contain only the product step described there, with
> no content the ladder says must not appear yet.
> If the concern bundles workflow/provider variants, relies on shared helpers
> as a keep-together reason, or owns a risky whole file without a single
> behavior slice, split the plan before peeling.
> If the concern's `expected_commits` or `internal_slices` describe multiple
> independently coherent behavior slices, stop and split the plan before
> peeling. Do not create a batch that merely promises to split later during
> rebuild.
> Use `git-stage-batch discard --to decompose-NN-NAME --no-auto-advance` for
> original concern content. Pass `--no-auto-advance` on every mutating
> `git-stage-batch` command that supports it, then run a fresh `show` before
> using any line IDs. Do not use `save` to capture concern content.
> For each concern, draft the one-clause batch note before selecting lines.
> If no narrow note fits, split the concern. Before the first `discard --to`
> or `include --to`, run `git-stage-batch new decompose-NN-NAME --note
> "One-clause purpose"` so the batch never exists as `Auto-created`. Do this
> before bulk skips, broad traversal, repairs, or verification. Use the note
> as the selection contract.
> When a concern owns entries inside a shared import group, registry, parser
> container, list, table, or Markdown section, copy the shared wrapper/context
> into the same concern batch with `git-stage-batch include --to
> decompose-NN-NAME --no-auto-advance`; then discard only the owned entries.
> Do not discard dependency-owned context from the working tree just to make
> the batch parse. For modest syntax-fixing replacements, prefer
> `--as-stdin` over `--as`.
> When deferring many unrelated files, use `git-stage-batch skip --files
> PATTERN... --no-auto-advance` with gitignore-style patterns instead of a
> long one-file skip loop, but only when every matched file is outside the
> current concern.
> Each concern batch must be a replayable unit before moving to the next
> concern: inspect its `refs/git-stage-batch/state/...:batch.json` metadata,
> materialize every Python file from `refs/git-stage-batch/batches/...`, and
> run `python -m py_compile` on those materialized files.
> Checkpoint every concern. Before peeling it, run
> `python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-running --current-batch decompose-NN-NAME`.
> After its batch and optional repair batch pass audit, run
> `python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-running --completed-batch decompose-NN-NAME`.
> Peel complete syntactic units, not moving line-range fragments: complete
> parser calls, complete function/test blocks, complete dispatch branches, and
> complete Markdown sections.
> Stop the git-stage-batch session when deconstruction is complete.

### Gate 2: Validate the batch list

After Phase 2 completes:

```bash
git-stage-batch list
git-stage-batch status
```

Then inspect batch metadata through git refs only. Do not use
`git-stage-batch show` for this gate:

```bash
python - <<'PY'
import json, pathlib, re, subprocess, sys, tempfile
refs = subprocess.check_output(
    ["git", "--no-optional-locks", "for-each-ref", "--format=%(refname)", "refs/git-stage-batch/state"],
    text=True,
).splitlines()
bad = []
for ref in refs:
    try:
        raw = subprocess.check_output(
            ["git", "--no-optional-locks", "cat-file", "-p", f"{ref}:batch.json"],
            text=True,
        )
    except subprocess.CalledProcessError:
        continue
    batch = json.loads(raw)
    name = batch.get("batch", ref.rsplit("/", 1)[-1])
    if not name.startswith("decompose-"):
        continue
    note = batch.get("note", "")
    if not note or note == "Auto-created":
        bad.append(f"{name}: missing deliberate single-purpose note")
    if re.search(r"\b(all|full|complete|entire|shared|mixed|integration)\b", note, re.I):
        bad.append(f"{name}: broad batch note: {note}")
    if re.search(r"\b(and|also)\b|as well as|;", note, re.I):
        bad.append(f"{name}: batch note has multiple actions: {note}")
    if "|" in note:
        bad.append(f"{name}: batch note hides variants with |: {note}")
    content_ref = batch.get("content_ref") or f"refs/git-stage-batch/batches/{name}"
    files = batch.get("files", {})
    for path, meta in files.items():
        claims = [
            claim
            for presence in meta.get("presence_claims", [])
            for claim in presence.get("source_lines", [])
        ]
        if path.endswith((".py", ".md", ".toml", ".yaml", ".yml", ".json")) and any(
            re.search(r"\b1-\d{3,}\b", claim) for claim in claims
        ):
            bad.append(f"{name}: large whole-file claim in {path}: {', '.join(claims)}")
        if path.endswith(".py"):
            try:
                source = subprocess.check_output(
                    ["git", "--no-optional-locks", "show", f"{content_ref}:{path}"],
                    text=True,
                    stderr=subprocess.PIPE,
                )
            except subprocess.CalledProcessError as exc:
                bad.append(f"{name}: cannot read batch Python file {path}: {exc.stderr.strip()}")
                continue
            with tempfile.TemporaryDirectory() as tmp:
                tmp_path = pathlib.Path(tmp) / path.replace("/", "__")
                tmp_path.write_text(source, encoding="utf-8")
                proc = subprocess.run(
                    [sys.executable, "-m", "py_compile", str(tmp_path)],
                    text=True,
                    capture_output=True,
                )
                if proc.returncode != 0:
                    lines = (proc.stderr or proc.stdout or "py_compile failed").strip().splitlines()
                    detail = lines[-1] if lines else "py_compile failed"
                    bad.append(f"{name}: batch Python file does not compile: {path}: {detail}")
if bad:
    print("\n".join(f"- {item}" for item in bad), file=sys.stderr)
    sys.exit(1)
PY
```

Check:

1. Every planned concern has a corresponding `decompose-NN-NAME` batch.
2. No batch has an artifact-category name.
3. No unnumbered original-content holding batches exist.
4. `git-stage-batch status` reports no active or completed session.
5. The working tree contains only the minimal base.
6. If Phase 2 changed the plan, `$DECOMPOSE_STATE_DIR/decompose-plan.json` has been
   updated and Gate 1 passes again against the revised plan.

If any check fails, report the failure. Do not proceed to Phase 3 with
a known-broad or missing batch.

Then checkpoint the passed deconstruction:

```bash
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-complete --note "Gate 2 passed"
```

## Phase 3: Rebuild

Spawn `Agent(decompose-rebuilder)` with this prompt:

> Rebuild the commit series from the batches. Apply batches in reverse
> order (innermost first). The base commit is {BASE_SHA}.
> The concern plan is at `$DECOMPOSE_STATE_DIR/decompose-plan.json`.
> The evolution narrative is at `$DECOMPOSE_STATE_DIR/decompose-narrative.md`.
> Compute `DECOMPOSE_STATE_DIR` with the checkpoint helper if it is not
> already set.
> Before using git-stage-batch, read the installed help for the top-level
> command and each subcommand you will use. Use only documented commands,
> selectors, flags, and argument shapes. If the help output does not show a
> syntax, do not use it.
> Before applying any batch, run
> `python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase3-running`.
> Read CONTRIBUTING.md and .git/hooks/commit-msg before committing.
> Read the narrative and the plan's `evolution_ladder` before applying any
> batch. Each restored concern must be committed as the next believable
> simplified product state, not as a finished file or module snapshot.
> Important: `git-stage-batch apply --from BATCH` writes to the working tree
> only; it does not write to the index. After applying a batch, inspect the
> unstaged diff and stage one atomic commit at a time using the
> `commit-unstaged-changes` strategy. Do not treat applying a batch as staging
> a batch.
> Commit in behavior/proof pairs: implementation slice, narrow tests or proof,
> next implementation slice, next narrow proof. Do not create all
> implementation commits first and all test commits afterward. If a source
> commit needs several later test commits, split the source commit.
> Checkpoint rebuild progress. Before applying each batch, run
> `python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase3-running --current-batch decompose-NN-NAME`.
> After every commit, run
> `python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase3-running --commit HEAD`.
> After all commits for that batch are complete and the batch is dropped, run
> `python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase3-running --completed-batch decompose-NN-NAME`.
> Each commit must be verified as the committed snapshot, not through the dirty
> working tree that still contains future changes. After every commit, run
> `.claude/skills/decompose-and-commit-unstaged-changes/scripts/verify-head-snapshot.py`
> through `python` with a check appropriate to the touched surface, starting
> with `python -m compileall -q src tests` and expanding to relevant pytest
> subsets or the repository's full test command for behavior, import, packaging,
> or orchestration changes. If any committed snapshot fails, amend or rewrite
> the offending commit before continuing. Do not accumulate late repair commits.
> After all batches are committed, run the final evolution split audit before
> Gate 3. Re-read the actual series as a story of incremental development:
> every commit should be the next coherent product state, not a finished
> subsystem dropped into place. Split any commit that still contains separable
> concerns, sublayers, adapters, docs, tests, fixtures, build changes, or
> coordinator paths. Then run the final history-polish pass. Any late
> repair/process commit must be integrated into the commit where the repaired
> behavior first belonged, using the explicit interactive rebase commands in
> Safety and Recovery. Do not report success with a tail commit that restores
> content lost during decomposition.

### Gate 3: Definition of Done

After Phase 3 completes, run all of these checks:

```bash
git --no-optional-locks status --short
git-stage-batch list
git-stage-batch status
```

**All must pass:**

1. `git status` has no unexplained tracked or untracked leftovers.
2. `git-stage-batch list` is empty of every batch created for this workflow.
   If it prints any `decompose-*` batch, drop it and re-check.
3. `git-stage-batch status` reports no active or completed session.
   If it does, stop the session and re-check.
4. The repository's normal test command passes (or a concrete external
   blocker is reported).
5. If `.gitmodules` exists, every listed submodule path appears in
   `git ls-tree HEAD` with mode `160000`. First list the paths, then
   substitute every returned path into `ls-tree`; do not use a fixed path
   list:

```bash
git config --file .gitmodules --get-regexp '^submodule\..*\.path$'
git --no-optional-locks ls-tree HEAD PATH_FROM_GITMODULES ...
```

Do not run the placeholder literally; replace it with every path printed by
the first command.

6. No commit subject in the series contains `and`, `also`, `as well as`,
   a semicolon, or two independent actions:

```bash
git --no-optional-locks log --format='%s' {BASE_SHA}..HEAD
```

   Scan each subject. If any violates the single-action rule, the series
   is not complete.

7. No commit in the series is a schema dump, coordinator dump, or
   artifact-category commit. Check for subjects starting with broad
   patterns like "Add shared", "Add all", "Wire all", "Cover all",
   "Add data models for", or "Add ... and ...".
8. No subject merely names the artifact added. A valid subject names the
   behavior, invariant, workflow, or contract that becomes true after the
   commit. Subjects shaped like `Add MODULE`, `Cover MODULE`, `Register X`,
   `Expand README`, or `Wire integration` must be rewritten or the commit must
   be split unless that exact action is the product-level outcome.
9. No commit creates a new non-generated code or test file over 600 lines as
   one finished artifact. Split large files into internal behavior slices with
   nearby proof.
10. The final evolution split audit has been performed after all batches were
    rebuilt. For every commit in `{BASE_SHA}..HEAD`, inspect its subject, body,
    diffstat, and patch. If a commit can be split into a smaller coherent
    groundwork commit, behavior slice, adopter, test proof, docs update,
    fixture slice, build-system slice, or coordinator call path, split it with
    the committed-snapshot split procedure below before success.
11. No commit is a late repair/process commit. Scan the full series for
    subjects or bodies containing `restore`, `repair`, `lost`,
    `decomposition`, `batch`, `fixup`, or `cleanup`, and inspect any `fix:`
    commit that appears after the feature it repairs:

```bash
BASE_SHA=PUT_BASE_SHA_HERE python - <<'PY'
import os, re, subprocess, sys
base = os.environ["BASE_SHA"]
log = subprocess.check_output(
    ["git", "--no-optional-locks", "log", "--reverse", "--format=%H%x00%s%x00%b%x00END", f"{base}..HEAD"],
    text=True,
)
bad = []
for entry in log.split("\x00END\n"):
    if not entry.strip():
        continue
    sha, subject, body = (entry.split("\x00", 2) + [""])[:3]
    text = subject + "\n" + body
    if re.search(r"\b(restore|repair|lost|decomposition|batch|fixup|cleanup)\b", text, re.I):
        bad.append(f"{sha[:12]} {subject}")
if bad:
    print("late repair/process commits must be integrated into earlier commits:")
    print("\n".join(bad))
    sys.exit(1)
PY
```

    If this finds anything, use the final repair integration procedure below.

If any check fails, report the specific failure. Do not report success
while holding unfinished-work signals.

Then record completion:

```bash
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase3-complete --note "Gate 3 passed"
```

## Completion Report

After all gates pass, report:

- How many concerns were identified
- How many commits were created
- The subject line of each commit in series order
- Which commits were split during the final evolution split audit, or that no
  split candidates remained
- Any manual repairs or plan adjustments that were needed

## Safety and Recovery

### During deconstruction

- If a discard goes wrong: `git-stage-batch undo`
- If the tree is unrecoverable: `git-stage-batch abort`
- Always verify coherence after each peel

### During rebuild

- If `apply --from` or `discard --from` fails: inspect the batch and repair
  the working tree manually
- If a commit fails before it is created due to a hook: fix and retry the same
  commit
- If a committed snapshot fails after creation: amend or rewrite that offending
  commit before continuing; never leave the breakage for a later repair commit
- If the tree is inconsistent: repair before committing

#### Rewrite a failed committed snapshot

Do not add a later repair commit. Use one of these command sequences.

If the newest commit fails, amend `HEAD` immediately. This is the expected
recovery path because Phase 3 verifies after every commit:

```bash
git --no-optional-locks status --short
git-stage-batch start
git-stage-batch show
git-stage-batch include --line FIX_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git commit --amend --no-edit
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/verify-head-snapshot.py --ref HEAD -- python -m compileall -q src tests
```

Use `git-stage-batch include --file PATH_WITH_FIX --no-auto-advance` only when
every change in that path belongs in the failed commit. For modest replacement
edits, prefer `--as-stdin`:

```bash
cat <<'EOF' | git-stage-batch include --line FIX_LINE_IDS --as-stdin --no-auto-advance
replacement text
EOF
```

If the subject/body also needs correction, replace `--no-edit` with this form:

```bash
git commit --amend -m "$(cat <<'EOF'
prefix: Corrected summary

Corrected body.
EOF
)"
```

If an older commit fails during a final audit, first require a clean working
tree. Do not start a rebase while future unstaged work is present:

```bash
git --no-optional-locks status --short
```

Find the first failing commit:

```bash
BASE_SHA=PUT_BASE_SHA_HERE
for c in $(git --no-optional-locks rev-list --reverse "$BASE_SHA"..HEAD); do
  python .claude/skills/decompose-and-commit-unstaged-changes/scripts/verify-head-snapshot.py --ref "$c" -- python -m compileall -q src tests || { echo "FIRST_BAD=$c"; break; }
done
```

Edit that commit with a non-interactive interactive rebase:

```bash
BAD_SHA=PUT_FIRST_BAD_SHA_HERE
GIT_SEQUENCE_EDITOR="sed -i '1s/^pick /edit /'" git rebase -i "$BAD_SHA^"
```

When rebase stops, stage the minimal fix, amend, verify, and continue:

```bash
git-stage-batch start
git-stage-batch show
git-stage-batch include --line FIX_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git commit --amend --no-edit
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/verify-head-snapshot.py --ref HEAD -- python -m compileall -q src tests
git rebase --continue
```

If conflicts occur, resolve them, use `git add` only to mark the resolved
conflict paths, and run `git rebase --continue`. That conflict-resolution
bookkeeping is not a staging method for ordinary commit slices. After the
rebase finishes, re-run the verification loop over `BASE_SHA..HEAD` and repeat
for the next first failing commit if needed.

#### Split a broad committed snapshot

After the rebuild is complete, do one final split round over the actual commit
series. The goal is not smaller commits for their own sake; the goal is a
history that reads like a maintainer evolved the project one coherent product
state at a time.

Start with a clean tree and write the audit under the workflow state directory,
not `.claude`:

```bash
export DECOMPOSE_STATE_DIR=$(python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py state-dir)
mkdir -p "$DECOMPOSE_STATE_DIR"
git-stage-batch block-file --local-only .git-stage-batch/
git --no-optional-locks status --short
BASE_SHA=PUT_BASE_SHA_HERE
git --no-optional-locks log --reverse --format='%H %s' "$BASE_SHA"..HEAD > "$DECOMPOSE_STATE_DIR/final-split-audit.txt"
```

For every commit in the range, inspect both the message and patch:

```bash
COMMIT_SHA=PUT_COMMIT_SHA_HERE
git --no-optional-locks show --stat --summary --find-renames "$COMMIT_SHA"
git --no-optional-locks show --patch --find-renames "$COMMIT_SHA"
```

Treat a commit as a split candidate when any of these are true:

- The subject or body lists multiple outcomes, even without using `and`.
- The diff adds a finished module, coordinator, command, docs section, fixture
  tree, or build-system surface that could have had a smaller first version.
- One commit mixes groundwork with the first adopter, or one adopter with
  later adopters.
- A source commit contains tests for several behaviors, or a test commit covers
  unrelated behavior that could prove earlier commits.
- Docs or examples describe behavior that was not true immediately after the
  preceding commit.
- A file's final shape appears all at once instead of visibly accreting across
  the series.
- The subject names one concern but the patch contains another meaningful
  concern that is not required support for the named outcome.

Bad pattern: a commit titled like "Reconstruct Jira fixtures" also adds git
source repository capture, Jira miss discovery, issue fixture writing, search
fixture writing, starting-issue stubs, and dev-status capture. Better pattern:
commit source repo capture, miss discovery, issue fixtures, search fixtures,
starting stubs, and dev-status as separate product states.

If the commit can be split, rewrite it mechanically. Mark the broad commit for
editing. For one split candidate, rebase from that commit's parent; do not
rebase the whole range from `BASE_SHA` just to edit one later commit because
previous rewrites can trigger skipped-cherry-pick behavior and widen the
conflict surface:

```bash
SPLIT_SHA=PUT_BROAD_COMMIT_SHA_HERE
GIT_SEQUENCE_EDITOR="sed -i '1s/^pick /edit /'" git rebase -i "$SPLIT_SHA^"
```

Use a `BASE_SHA` todo only when deliberately editing several commits in one
rebase.

When rebase stops at the broad commit, uncommit it into the working tree:

```bash
git reset --mixed HEAD^
git --no-optional-locks status --short
git --no-optional-locks diff --stat
git-stage-batch start
```

Plan the replacement mini-series before staging. Each replacement commit must
answer:

- What smaller product state exists after this commit?
- What exact earlier state does it evolve?
- What proof lands with or immediately after the behavior?
- Which final-tree content must still be absent?
- Will this commit be assembled from original hunks, or from a reconstructed
  whole-file snapshot that better represents the intended intermediate state?
- Which remaining diff content is a separate later outcome rather than support
  for this commit?

Do not force the original diff's hunk boundaries to define the new history. A
large move/modify patch, generated fixture, rewritten docs section, or
finished coordinator may need a whole-cloth intermediate version before the
final file shape. Line-level staging being awkward is not a reason to keep a
broad commit; it is a reason to reconstruct the next smaller product state
and stage that snapshot deliberately.

Stage and commit one sublayer at a time with `git-stage-batch`.

When the original hunks already match the desired sublayer, use line
selection:

```bash
git-stage-batch show
git-stage-batch include --line SUBLAYER_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git commit
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/verify-head-snapshot.py --ref HEAD -- python -m compileall -q src tests
git-stage-batch stop
git --no-optional-locks status --short
# If unstaged changes remain for another sublayer, start the next review pass:
git-stage-batch start
```

When the desired sublayer is clearer as rewritten file content, reconstruct
that file in the working tree, then stage exactly that snapshot through
`include --file --as-stdin`:

```bash
# Edit PATH so it contains only the file content that should exist after
# this replacement commit, not the final all-features version.
git-stage-batch show --file PATH --no-advance
git-stage-batch include --file PATH --as-stdin --no-auto-advance < PATH
git --no-optional-locks diff --cached -- PATH
git --no-optional-locks diff --cached --check
git commit
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/verify-head-snapshot.py --ref HEAD -- python -m compileall -q src tests
git-stage-batch stop
git --no-optional-locks status --short
# If unstaged changes remain for another sublayer, start the next review pass:
git-stage-batch start
```

Whole-file reconstruction is allowed to simplify a tangled historical patch,
not to hide multiple outcomes in one commit. The staged file must represent
one smaller product state, and later commits must visibly evolve it toward the
final tree. For Python whole-file snapshots, compile is only a minimum check;
also run the narrow behavior test or import/use path that would catch missing
runtime names introduced by the reconstructed intermediate file.

After each replacement commit, inspect the remaining unstaged diff as a new
split candidate before committing it. Do not treat the residue as one final
commit by default. If the residue contains another helper family, result field,
fixture family, REST surface, docs section, or adopter that can land after the
current committed state, split again.

Use the same commit-message standards and `commit-message-drafter` inputs as
normal rebuild commits. Prefer this order: minimal groundwork, first consumer,
narrow proof, next consumer, narrow proof, docs/examples only after the
documented behavior exists. Do not recreate the original broad commit under a
vague summary. Write commit bodies with the project's line-length and wording
rules in mind; if the commit hook rejects only the message, retry the same
staged diff with a corrected message.

When the broad commit has been fully replaced and `git status --short` is
clean, continue the rebase:

```bash
git --no-optional-locks status --short
git rebase --continue
```

After the rebase finishes, rerun the final split audit from the beginning.
History rewriting changes later SHAs and can reveal new split candidates. Stop
only when a full pass finds no remaining commit that can be split into a
better incremental evolution.

#### Integrate late repair commits

Gate 3 must not leave a tail commit whose purpose is to restore, repair,
recover, clean up, or compensate for the decomposition. Integrate each such
commit into the earlier commit where the hunk first belonged, then drop the
repair commit.

First require a clean tree and list suspicious commits:

```bash
export DECOMPOSE_STATE_DIR=$(python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py state-dir)
mkdir -p "$DECOMPOSE_STATE_DIR"
git-stage-batch block-file --local-only .git-stage-batch/
git --no-optional-locks status --short
BASE_SHA=PUT_BASE_SHA_HERE
git --no-optional-locks log --reverse --format='%H %s' "$BASE_SHA"..HEAD
```

For each suspicious repair commit, inspect the diff and write down the target
commit for each hunk:

```bash
REPAIR_SHA=PUT_REPAIR_SHA_HERE
git --no-optional-locks show --stat --patch --find-renames "$REPAIR_SHA"
git --no-optional-locks log --reverse --format='%H %s' "$BASE_SHA"..HEAD -- PATH_TOUCHED_BY_REPAIR
```

If one repair commit contains hunks for different historical points, split the
allocation by hunk. Do not squash the whole repair into a convenient nearby
commit unless every hunk first belongs there. If any hunk cannot be placed
confidently, fail the workflow instead of keeping a repair commit.

For a one-target repair, use this exact pattern. It edits the target commit,
drops the later repair commit from the todo list, and leaves you stopped at the
target so you can amend it:

```bash
BASE_SHA=PUT_BASE_SHA_HERE
TARGET_SHA=PUT_COMMIT_THAT_SHOULD_HAVE_CONTAINED_THE_HUNK
REPAIR_SHA=PUT_REPAIR_COMMIT_TO_DROP
TARGET_SHORT=$(git rev-parse --short=7 "$TARGET_SHA")
REPAIR_SHORT=$(git rev-parse --short=7 "$REPAIR_SHA")
git --no-optional-locks show --format= --binary "$REPAIR_SHA" > "$DECOMPOSE_STATE_DIR/repair-$REPAIR_SHORT.patch"
GIT_SEQUENCE_EDITOR="sed -i -E -e 's/^pick (${TARGET_SHORT}[0-9a-f]*) /edit \\1 /' -e 's/^pick (${REPAIR_SHORT}[0-9a-f]*) /drop \\1 /'" git rebase -i "$BASE_SHA"
```

When rebase stops at the target commit, apply only the hunk or hunks that
belong in that target. Prefer `git apply --3way` for modest whole-patch
repairs; otherwise edit manually. Stage the selected repair hunk with
`git-stage-batch`, not Git's built-in partial staging:

```bash
git apply --3way "$DECOMPOSE_STATE_DIR/repair-$REPAIR_SHORT.patch" || true
git-stage-batch start
git-stage-batch show
git-stage-batch include --line TARGET_HUNK_LINE_IDS --no-auto-advance
git --no-optional-locks diff --cached
git commit --amend --no-edit
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/verify-head-snapshot.py --ref HEAD -- python -m compileall -q src tests
git rebase --continue
```

For a repair commit whose hunks belong in several earlier commits, repeat the
same pattern with all target commits marked `edit` and the repair commit marked
`drop`. At each stop, stage only the hunks allocated to that target, amend,
verify, and continue. After the rebase finishes, rerun Gate 3 from the
beginning.

### Preserving work

- Do not run destructive git commands without checking with the user
- Batches are the source of truth during rebuild — do not drop them until
  committed

## Expected Workload

This workflow is intentionally operation-heavy. A correct run may require
hundreds of `git-stage-batch` operations. That is expected progress, not a
reason to broaden the decomposition.
