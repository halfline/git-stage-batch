# Preparation, execution, and review

Read this reference with the current phase brief. Phase 1 prepares boundaries
and order; Phase 2 preserves source in batches; Phase 3 authors atomic history.
Constructing a complete history to justify a plan duplicates reconstruction.

## Concerns and atomic slices

Each atomic commit has one reason to exist. A narrow concern can contain a
small sequence such as an internal contract, its required separate proof,
and documentation of that contract. Different operations, providers, behavior
branches, or persisted shapes are candidates for separate concerns. A feature
can span many concerns. Neither effort nor commit count settles the boundary.

Inspect real changed regions. Several functions can implement one change;
one function can contain independent changes. Shared files, dependencies,
syntax, file size, definition counts, and subject punctuation cannot establish
atomicity. Separate a provider and its adopter when each has a useful contract.
Keep a transition together when the actual contract requires it.

Identify independent implementation slices before peeling. A batch cannot be
a broad holding bucket with a promise to split it later. Reconstruction can
refine an already narrow concern as its real earlier implementations become
clear; that discovery updates the local mini-series, not the entire plan.

Without a repository convention requiring separate proof, focused tests and
supporting material establishing the same contract normally accompany it.
Where tests must be separate `tests: Validate ...` commits, place them
immediately after their code, before unrelated implementation. Record that
link in `expected_commits`. Test/docs/mechanical work for existing behavior
can be an independent concern and need not invent new implementation. A
verification slice for that case uses `validates: "base:PATH:CONTRACT"`; semantic
review confirms that the named contract already exists in the recorded base.

## Plan the chronology before executing it

Prepare a concise ladder of working milestones from the committed base to the
intended source. Group related narrow concerns around their first useful flow:
provider, adopter, and proof near one another when dependencies permit.
Useful internal contracts can precede consumers. A milestone can span several
concerns and commits; it is not a feature-sized commit.

Describe what shared functions/imports/registries will need to lose during
peeling and how they will grow during reconstruction. Final imports inventory
the target; their presence alone does not require those imports in the first
historical version. Avoid exposing controls before handlers or loading a later
provider before its first use. Inspect chronology during Gate 1 to catch long
avoidable runs of unused modules or delayed integrated proof.

Phase 1 describes these versions without writing them. During reconstruction,
stage simpler complete implementations with `include --line --as-stdin` or
`include --file --as-stdin`. Generate valid lockfiles for the dependency set
actually being committed; do not divide generated lockfile lines arbitrarily.
No placeholders or lazy imports may hide a real missing dependency.

## Preparation plan

`scripts/decompose-plan.py capture` creates a uniquely named input directory
and state-relative manifest. Its digest binds the plan to that input.
All nonignored changed paths, including deletions, symlinks, executable modes,
and gitlinks, need owners. Track dirty submodule work separately. Captured
blobs and the binary diff preserve input; they are not an alternate staging or
batch mechanism.

Use schema 2. Numbers describe outermost-to-innermost peel order, with providers
having higher numbers. Stable slugs survive renumbering. The chronological
ladder is independent of concern count. Example:

```json
{
  "schema": 2,
  "base": "FULL_BASE_COMMIT_ID",
  "input_manifest": "decompose-input-RUN_ID/manifest.json",
  "input_digest": "SHA256_OF_MANIFEST_BYTES",
  "evolution_ladder": [
    {"step": 1, "behavior_after": "Decode records directly"},
    {"step": 2, "behavior_after": "Decode a supplied record from the CLI"}
  ],
  "concerns": [
    {
      "number": 1,
      "name": "decompose-01-decode-command",
      "slug": "decode-command",
      "purpose": "Decode a record from the CLI",
      "evolution_step": 2,
      "depends_on": [2],
      "dependency_evidence": [
        {"provider": 2, "anchor": "cli.py: decode command", "contract": "decode(record)"}
      ],
      "expected_commits": [
        {"slug": "command", "purpose": "Expose record decoding", "role": "implementation"},
        {"slug": "proof", "purpose": "Validate CLI decoding", "role": "verification", "validates": "decode-command/command"}
      ]
    },
    {
      "number": 2,
      "name": "decompose-02-record-decoder",
      "slug": "record-decoder",
      "purpose": "Decode the documented record format",
      "evolution_step": 1,
      "depends_on": [],
      "dependency_evidence": [],
      "expected_commits": [
        {"slug": "decoder", "purpose": "Decode records", "role": "implementation"},
        {"slug": "proof", "purpose": "Validate record decoding", "role": "verification", "validates": "record-decoder/decoder"}
      ]
    }
  ],
  "ownership_ledger": [
    {"path": "decoder.py", "anchor": "decode", "concern": 2, "kind": "owned"},
    {"path": "test_decoder.py", "anchor": "decoder tests", "concern": 2, "kind": "owned"},
    {"path": "cli.py", "anchor": "handler/import/registration", "concern": 1, "kind": "owned"},
    {"path": "test_cli.py", "anchor": "command tests", "concern": 1, "kind": "owned"}
  ],
  "peel_order": [1, 2],
  "rebuild_order": [2, 1]
}
```

Adapt proof placement to the repository. Ledger anchors identify actual regions
once as owned; shared syntax is `kind: "context"` and can recur. Several concerns
can own distinct regions in a file. The helper checks path coverage, dependency
order, milestones, and proof adjacency. Semantic review must establish region
coverage, meaningful boundaries, and real dependency contracts.

## Local correction and convergence

Record findings by stable slug, actual disputed regions/outcomes, and required
correction. Collect independent findings in one review pass. Accepted boundaries
remain accepted until changed source, a changed prerequisite, an order inversion,
or concrete contrary evidence invalidates them. Do not reopen them for a new
reviewer's preference or regenerate all concerns after one objection.

Save the prior plan before editing. `decompose-plan.py affected OLD NEW` reports
changed boundaries, order inversions, and dependency/shared-file successors.
It ignores number/name-only changes. Use that report to scope semantic review;
run the cheap global structural validator after each plan edit. Complete global
semantic review at phase boundaries, reusing established findings for unchanged
regions. If the helper conservatively flags a shared file, inspect the affected
regions before repeating expensive checks.

For a repeated objection, put the same concrete diff and proposed narrower
boundary in front of the author and reviewer. Resolve the discrepancy in the
current phase. Once execution has begun, any necessary experiment uses the
tool's selection and replacement operations. A schema error needs a schema
correction; an independent behavior needs a split. A precise tool limitation,
missing prerequisite, or unresolved unsafe boundary is a blocker. Elapsed
effort and remaining volume are reasons to continue, not broaden history.

## Verification and reuse

Gate 1 reviews preparation. Gate 2 proves actual batch preservation and replay.
Phase 3 verifies real committed contracts. Final refinement verifies its
resulting history. Match each check to its claim; syntax/import/collection
checks cannot establish behavior. Docs/mechanical changes can use inspection.

Use the decompose `scripts/verify-head-snapshot.py` helper for grouped commands
in one clean detached checkout with immutable logs and receipts. A check
specification lists `commands` (argument lists), optional `setup` commands,
`environment` overrides, external file `inputs`, and runtime/dependency
`prerequisites`. Run setup from the snapshot's own manifests/lockfiles.
External proof harnesses live outside source and are listed in `inputs`.
For separate code/test commits, the code check can use the next test's assertions
only if the harness invokes code and prerequisites already present.
Allocate checks by atomic slice during preparation. Test discovery for a future
file can report success with zero tests; require meaningful assertions against
the present implementation, then run the new test after its proof commit.

Receipts bind the actual tree, commands, runner, executable contents, complete
environment digest, declared prerequisite identities, external input contents,
and output logs. Commit IDs or concern numbers alone do not bind execution.
`--reuse` checks this identity and log integrity, including after rewording.
Declare every external dependency; if its current identity cannot be established,
run fresh. A matching failed attempt is reported without blindly rerunning it;
`--retry "diagnosed reason"` retains a new attempt after a runner correction or
a verified transient condition. A changed source tree needs new proof.

Run all commands for one snapshot together. Reuse dependency/download caches
keyed to pinned inputs, while imports come from the isolated source. Generated
outputs must be ignored; setup/checks may not alter tracked source or introduce
untracked source. Run focused unit/CLI/build checks for their contracts and real
browser scenarios where browser behavior needs proof. Limit concurrent browser
runs under memory pressure. Do not recreate a browser and dependency environment
for every assertion.

Hand verified receipts to final refinement rather than rerun unchanged checks
by phase name. Reuse does not replace batch replay, final object/ownership
validation, the final repository checks, or required semantic review.
