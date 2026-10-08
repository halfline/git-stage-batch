---
name: decompose-and-commit-unstaged-changes
description: Decompose unstaged working-tree changes into narrow concerns, peel them into git-stage-batch batches, and rebuild a fine-grained atomic commit series
user-invocable: true
disable-model-invocation: true
context: fork
when_to_use: "Use when the user wants to decompose unstaged working-tree changes into a clean commit series by identifying product, workflow, or architectural concerns, peeling them away one by one into concern-named batches, reducing the working tree to a minimal base, and then rebuilding by applying batches in reverse order with one atomic commit per concern. Examples: \"decompose and commit unstaged changes\", \"decompose into a commit series\", \"peel this into layered commits\", \"build this up brick by brick\"."
allowed-tools:
  - Read
  - Grep
  - Glob
  - LS
  - Agent(decompose-analyzer)
  - Agent(decompose-deconstructor)
  - Agent(decompose-rebuilder)
  - Bash
---

# Decompose and Commit Unstaged Changes

Build an atomic commit series from unstaged work through analysis, peeling,
and rebuilding. Each concern is one reviewable change; a feature can span many
concerns. There is no target commit count.

Read `references/decompose-planning.md` before any phase. It defines concrete
snapshot evidence, atomicity, proof, and targeted corrections. Phase briefs:

- `.claude/agents/decompose-analyzer.md`
- `.claude/agents/decompose-deconstructor.md`
- `.claude/agents/decompose-batch-peeler.md`
- `.claude/agents/decompose-rebuilder.md`

Delegate phases to their named agents. Give Gate 1 reviewers actual patches,
input evidence, and precise findings rather than an expected conclusion.

## Usage

```text
/decompose-and-commit-unstaged-changes
/decompose-and-commit-unstaged-changes deconstruct
/decompose-and-commit-unstaged-changes reconstruct
/decompose-and-commit-unstaged-changes resume
```

Default runs all phases; `deconstruct` runs Phases 1–2; `reconstruct` starts
with existing verified batches at Gate 2; `resume` audits checkpoint state and
continues from the latest proven gate. Run autonomously within the user's
authorized scope. Do not require user approval of intermediate plans.

## Tools and state

Use `git-stage-batch` from PATH or `pipx run git-stage-batch`. Before using
it, read installed top-level and relevant subcommand help. Installed
documentation is authoritative; do not invent selectors, flags, commands, or
batch formats. Pass `--no-optional-locks` to read-only Git commands. Keep
maintained Git/batch mutations sequential. Use git-stage-batch for
maintained-index staging; native staging is allowed only in disposable
evidence repositories and for conflict resolution during an authorized rebase.

From the repository root:

```bash
REPO_ROOT=$(git --no-optional-locks rev-parse --show-toplevel)
cd "$REPO_ROOT"
export DECOMPOSE_STATE_DIR=$(python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py state-dir)
mkdir -p "$DECOMPOSE_STATE_DIR"
git-stage-batch block-file --local-only .git-stage-batch/
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py status --json
git-stage-batch status
git-stage-batch list
git --no-optional-locks status --short
git --no-optional-locks diff --cached
```

The default state directory is `$REPO_ROOT/.git-stage-batch/`. Keep manifests,
plans, narrative, patches, logs, and uniquely named prototype repositories
there. Respect `DECOMPOSE_STATE_DIR` overrides and block the actual state path
from review. Do not delete sibling recovery state.

For a fresh full/deconstruct run, an active session, staged work, or
preexisting `decompose-*` batches is a scope conflict: report the concrete
state rather than absorbing it. Existing verified batches are expected in
reconstruct or resume mode. Capture the base and start a fresh checkpoint:

```bash
BASE_SHA=$(git --no-optional-locks rev-parse HEAD)
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py start --mode full --base "$BASE_SHA"
```

Use `--mode deconstruct` for that mode. `start` clears only its own plan
files; uniquely named evidence directories from another run must never be
silently trusted. A correction within an active run does not call fresh
`start`.

## Resume

Inspect checkpoint status, current HEAD, maintained tree, and refs. Use the
checkpoint's original base. Artifacts without a valid checkpoint/provenance
need a fresh input audit.

- `phase1`: continue analysis from the inventoried input and any reusable
  evidence, then produce a candidate and run Gate 1.
- `gate1`: inspect the candidate and its input evidence; run Gate 1. Correct
  affected boundaries on failure, retaining valid unrelated evidence.
- `phase2-after-gate1`: run Gate 1 against the approved plan and current peel
  state, then continue only verified remaining concerns.
- `phase3-after-gate2`: replay Gate 2 from refs, then rebuild remaining batches.
- `gate3-or-manual-audit`: finish the required refine-history pass, using
  `refine-history resume` if its rewrite operation is active, then run Gate 3.
  Remaining batches route through Gate 2 instead.
- `fresh`: audit input and begin Phase 1.
- `complete`: revalidate refine-history completion and Gate 3 before reporting.

Changed source or prerequisites invalidate the affected snapshots and their
consumers, not automatically every planning decision. The complete gate still
checks the revised set. Never treat elapsed effort or an existing prose plan
as a passed gate.

## Phase 1: analysis

Spawn `Agent(decompose-analyzer)`.

Provide the base, mode, state directory, and any exact local review findings.
The worker reads the planning reference, inventories the real diff, captures
the final target, constructs before/after snapshots with checks, then writes
the candidate, concise narrative, and refinement evidence. Phase 1 cannot
change the maintained source, index, HEAD, or batch refs. Native commits in
the disposable evidence repository are permitted.

### Gate 1: proposed history

First perform structural validation. The command below checks actual retained
refs and patches, not semantic atomicity or successful execution of claimed
checks. It uses the candidate by default; set `DECOMPOSE_PLAN_PATH` to the
approved plan when validating it instead. Evidence paths are relative to the
workflow state directory.

```bash
python - <<'PY'
import json
import os
import subprocess
from pathlib import Path

state = Path(os.environ['DECOMPOSE_STATE_DIR']).resolve()
plan_path = Path(os.environ.get('DECOMPOSE_PLAN_PATH', str(state / 'decompose-plan.candidate.json')))
p = json.loads(plan_path.read_text())
def require(condition, message):
    if not condition:
        raise SystemExit(message)
def artifact(value):
    require(isinstance(value, str) and value, 'missing evidence path')
    path = (state / value).resolve()
    require(path.is_relative_to(state) and path.exists(), f'missing or external evidence: {value}')
    return path
repo = artifact(p['evidence_repo'])
def git(*args):
    return subprocess.check_output(['git', '-C', str(repo), '--no-optional-locks', *args])
def commit(value):
    actual = git('rev-parse', '--verify', value + '^{commit}').decode().strip()
    require(value == actual, 'snapshot must use an immutable full commit ID')
    return actual
base = commit(p['base'])
target = commit(p['target_commit'])
artifact(p['input_manifest'])
cs = p['concerns']
require(bool(cs), 'empty concern list')
numbers = list(range(1, len(cs) + 1))
require([c['number'] for c in cs] == numbers, 'noncontiguous concern numbers')
require(p['peel_order'] == numbers and p['rebuild_order'] == numbers[::-1], 'invalid orders')
require(len({c['slug'] for c in cs}) == len(cs), 'duplicate slug')
require(len({c['name'] for c in cs}) == len(cs), 'duplicate batch name')
ladder = p['evolution_ladder']
require([s['step'] for s in ladder] == numbers, 'invalid chronological ladder')
current = base
for step, c in enumerate(reversed(cs), 1):
    require(c['evolution_step'] == step, 'concern does not match chronological step')
    require(bool(c['purpose'].strip()), 'empty concern purpose')
    require(len(c['expected_commits']) == 1, 'promote independent commits to concerns')
    deps = c['depends_on']
    require(all(type(d) is int and c['number'] < d <= len(cs) for d in deps), 'invalid dependency order')
    require(set(deps) == {d['provider'] for d in c['dependency_evidence']}, 'missing dependency evidence')
    if c.get('role') == 'verification':
        validates = c.get('validates')
        require(type(validates) is int and validates == c['number'] + 1 and validates in deps, 'proof must immediately follow the code it validates')
    s = c['snapshot']
    before, after = commit(s['before']), commit(s['after'])
    require(before == current, 'broken snapshot chain')
    require(git('show', '-s', '--format=%P', after).decode().strip() == before, 'snapshot has wrong parent')
    require(git('rev-parse', before + '^{tree}') != git('rev-parse', after + '^{tree}'), 'empty concern patch')
    patch = git('diff', '--binary', '--full-index', '--no-ext-diff', '--no-textconv', before, after)
    require(artifact(s['patch']).read_bytes() == patch, 'patch differs from retained snapshots')
    require(isinstance(s['checks'], list), 'missing check records')
    require(s['checks'] or s.get('inspection'), 'missing proof or inspection')
    for check in s['checks']:
        require(check['snapshot'] == after and check['exit_code'] == 0, 'check does not pass on accepted snapshot')
        require(check['kind'] and isinstance(check['command'], list) and check['command'], 'invalid check command')
        artifact(check['log'])
    current = after
require(git('rev-parse', current + '^{tree}') == git('rev-parse', target + '^{tree}'), 'history does not reach captured target')
require(bool(p['ownership_ledger']), 'missing ownership ledger')
require(all(e['concern'] in numbers for e in p['ownership_ledger']), 'unknown ledger owner')
print('Snapshot structure and retained patches verified; semantic and source audits still required.')
PY
```

Then independently inspect the patches and relevant check output:

1. The input manifest matches the original base and intended final source,
   including all nonignored new files, deletions, modes, and gitlinks. On
   resume/after peeling, account for recorded commits, batches, and repairs.
2. Each patch has one reason to exist. Whole functions/files and support
   artifacts follow the atomicity rules in the planning reference. Disputed
   splits are resolved with narrower concrete snapshots and checks.
3. Dependencies reflect imports, calls, contracts, and registrations in the
   historical versions. Providers precede consumers; final imports do not
   force later features into early patches.
4. Owned regions cover the intended diff exactly once. Context is separate.
   Submodule stanzas and gitlinks have owners. Lockfiles/configuration are
   valid generated snapshots for their manifests.
5. Repository proof placement is honored, including required immediate
   `tests: Validate ...` commits with their code/proof links. Relevant checks
   actually ran in those snapshots with declared prerequisites.
   Syntax/import/collection output cannot stand in for behavioral proof.
   Generated ignored assets are built by the snapshot's scripts when required.
6. The concise narrative/ladder accurately describe the retained history.

A failed review gives precise concern/patch feedback. Apply targeted
correction as defined in the planning reference; retain valid unrelated
evidence. Rerun the complete gate. Do not approve a cosmetic rename that
conceals the same bundled patch, or restart from an untested outline after
every rejection.

After Gate 1 passes, copy the candidate to `decompose-plan.json` and mark:

```bash
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase1-complete --note "Gate 1 passed"
```

## Phase 2: peel

Spawn `Agent(decompose-deconstructor)`.

Provide the approved plan, input manifest, snapshots, and current state. Peel
in ascending concern order. Each batch already represents one atomic change;
the deconstructor must not defer unresolved splits to the rebuilder.

### Gate 2: batch replay

Require all of the following:

- every planned batch and recorded optional repair exists with its purpose;
- the maintained tree equals the recorded base, with a clean index;
- ref/ownership audits find no missing, unrelated, or partial owned content;
- actual reverse application in a disposable clone reproduces every approved
  after snapshot, reversing companion repairs, and reaches the captured target;
- relevant reconstructed-snapshot checks pass with their prerequisites;
- plan changes discovered during peeling pass the complete Gate 1;
- the live session has been stopped and no active/completed session remains.

Batch metadata and syntax checks alone cannot pass this gate. Correct affected
batches/snapshots on failure; do not rebuild a known-broad or unreplayable
batch.

```bash
git-stage-batch list
git-stage-batch status
git --no-optional-locks status --short
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase2-complete --note "Gate 2 passed"
```

Mark the checkpoint only after all checks pass. `deconstruct` ends here.

## Phase 3: rebuild

Spawn `Agent(decompose-rebuilder)`.

Provide the verified batch set, original base, plan, and replay evidence.
Restore batches in descending order, explicitly stage the approved change with
git-stage-batch, and create one atomic commit per concern. Required separate
`tests: Validate ...` concerns immediately follow their code concerns. Verify
every actual committed snapshot with an explicit appropriate check using
`.claude/skills/refine-history/scripts/verify-head-snapshot.py`. Follow
repository proof placement: include tests with their behavior by default, or
immediately afterward in the required separate test commit. Verify the code
snapshot using a retained harness/direct check when its committed test file
arrives next. Fix failed commits before continuing and record progress with
the checkpoint helper.

After rebuilding, read and invoke the companion
`.claude/skills/refine-history/SKILL.md` with the original base. Preserve the
proposed atomic boundaries unless concrete patch review finds a correction.
The required refine-history completion gate owns final message cleanup and any
history rewrite; do not duplicate it in the rebuilder.

```bash
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase refine-history-running --note "handing rebuilt series to refine-history"
```

If the companion skill is missing, report that exact installation blocker. Do
not claim completion or silently skip its gate.

### Gate 3: complete history

Require a clean maintained tree/index, no workflow batches or active/completed
session, exact final-tree equality with the captured target, the normal
repository test command passing, valid submodule gitlinks, and the
refine-history completion gate for `BASE_SHA..HEAD`. Generated ignored outputs
are prerequisites rather than unexpected tracked artifacts. Report concrete
external blockers without claiming the gate passed.

```bash
git --no-optional-locks status --short
git-stage-batch list
git-stage-batch status
python .claude/skills/decompose-and-commit-unstaged-changes/scripts/decompose-checkpoint.py mark --phase phase3-complete --note "Gate 3 passed"
```

Mark complete only after the gate. Report concern/commit counts, subjects in
series order, checks, refine-history result, and material repairs/blockers.

## Recovery

Keep batches until their content is committed and verified. Use installed
`undo` semantics for a mistaken peel and `abort` only after checking what work
it will restore/discard. Do not run destructive recovery over user work
without authorization. A failed newest commit is amended and reverified; older
failures require preserved dirty work and a clean local rewrite. No later
repair commit may conceal a broken intermediate snapshot.
