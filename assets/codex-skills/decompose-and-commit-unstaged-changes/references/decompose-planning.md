# Planning from proposed history

Read this reference in every phase. It defines concern boundaries, evidence,
and review corrections; worker briefs define the tool operations.

## Shape the development story

Plan a plausible sequence of working slices, not a dependency-layer inventory.
A slice may take several atomic commits: a provider with direct proof, its
adoption in a working flow, and a focused scenario. Keep those commits near
each other when dependencies allow. Shared foundations can land earlier when
they have their own useful contract; dependencies constrain order but do not
require every domain module before every UI module. Add integration or browser
proof near the first usable flow instead of collecting all such checks at the
end. A later broad regression suite can still be a separate concern.

Make the slices real by authoring simpler, complete historical versions when a
final file combines several flows. Evolve imports, handlers, markup, and
focused proof with each version so that snapshot works on its own. Reordering
final-file hunks or adding no-op scaffolding does not establish a working
slice; the versions must still reach the captured final tree.

At Gate 1, inspect the chronology as well as individual patches. Long runs of
unused providers, controls before their handlers, or delayed proof can reveal
an unnatural order even when every commit is atomic. Correct the affected
snapshots and checks without merging independent changes into feature commits.

## What is atomic

A concern is one reviewable change with one reason to exist. It can add an
internal API, alter a representation, fix an interaction, expose an existing
operation, change a build step, or document an existing contract. It need not
finish a feature or expose a new command. Pull requests can group concerns
into features later. There is no target concern or commit count.

Judge the actual patch. Several functions may implement one change; one
function may contain several changes. Follow repository commit conventions.
Without a separate-proof convention, source, focused tests, fixtures, and
documentation establishing the same contract normally belong together. Their
ability to exist separately does not automatically make them independent
outcomes.

When the repository requires separate `tests: Validate ...` commits, plan each
code change and its proof as adjacent concerns: code first, its tests
immediately afterward. Give the proof concern `role: "verification"` and
`validates` naming the code concern number; include that number in
`depends_on`. The pair establishes one contract, while each concern still
produces one commit. Do not merge code/proof against that convention or
collect all tests at the end. A narrow coverage/documentation change for
existing behavior is also a valid concern.

Different operations, providers, interaction behaviors, or persisted shapes
are candidates for separate commits. Shared files or helpers do not settle the
boundary. A dependency usually establishes order: a provider can land with
direct proof before a later adopter. Keep changes together when their contract
or transition requires the same patch, and explain that relationship. Do not
manufacture an import failure to justify keeping a feature together.

File size, definition count, punctuation, and words such as "shared" are
review prompts, not rejection rules. A subject should name one concrete
action, but grammar cannot prove atomicity. Do not split a coherent patch to
appease a word filter or merge independent changes under an umbrella subject.

## Capture the input once

Phase 1 leaves the maintained repository's working tree, index, HEAD, and
batch refs untouched. Record the base commit, tracked binary diff, untracked
nonignored paths with content hashes and modes, and submodule gitlinks in an
input manifest under `DECOMPOSE_STATE_DIR`. Exclude the workflow state itself.
Record ignored build/download outputs as prerequisites, not owned changes.
Respect the user's exclusion of generated assets from history.

Create a disposable Git repository under a uniquely named directory in the
workflow state directory. A local clone of the maintained repository supplies
the base objects. Capture the intended final tree there by applying the
tracked diff and copying only inventoried nonignored new files, including
deletions, executable bits, symlinks, and gitlinks. Do not copy the whole
dirty directory: that would bring in caches, downloads, and workflow state.
Record the captured target commit and compare its tree with the input
manifest.

Native Git staging and temporary commits are permitted only in that disposable
repository for constructing evidence. They are not final history and are not a
substitute for git-stage-batch during peeling or rebuilding. Keep the
disposable repository and evidence until the workflow completes. Do not write
prototype commits or refs into the maintained repository.

Before reusing evidence, compare the base and input manifest with the current
input. A changed file invalidates its boundaries and any snapshots, callers,
shared-file successors, or checks that consume the changed content. Unrelated
accepted boundaries remain useful, subject to the complete gate. Do not call
an entire rejected draft stale merely because one boundary failed review.
After peeling starts, compare against the captured target plus recorded batch
and remaining-tree state; the maintained tree is deliberately smaller then.

## Build the actual sequence

Inspect changed regions before outlining concerns. Draft small candidate
changes, then construct their chronological snapshots in the disposable
repository starting at the base. For each concern retain:

- immutable before and after commit IDs;
- a binary patch between them, relative to the workflow state directory;
- the observable change and owned regions, separately from shared context;
- dependencies supported by symbols or contracts used in that snapshot;
- executed checks, exact commands, prerequisites, exit codes, and log paths;
- any disputed split experiment and its result.

Each after commit has the corresponding before commit as its sole parent.
Consecutive concern snapshots form a chain; the last tree equals the captured
target. The commits are prototypes, so their messages need not be final commit
messages. Keep evidence attached to stable slugs and content hashes, not only
ordinal concern numbers. Renumbering alone does not invalidate a patch or
test.

For shared files, write complete valid earlier versions. A closure can first
support one screen and later grow another. A validator can first enforce the
document shell and later add maps. A test file can grow whole tests or classes
while its imports and fixtures grow with them. Syntax is a constraint on each
version, not an instruction to commit the final function or file whole.

Evolve imports, exports, parser registrations, dispatch tables, registries,
and documentation indexes with their actual consumers. Inspect both import and
call-time paths in each proposed snapshot, across the project's languages. The
final import graph is useful inventory, not the historical dependency graph.
Do not use lazy imports, unreachable branches, or placeholders to hide a
dependency that the introduced behavior actually invokes.

Generate lockfiles using the relevant package manager against each proposed
manifest. Preserve valid lockfile snapshots; assign the manifest/lock change
to its dependency concern instead of dividing generated lines arbitrarily.
Apply the same rule to generated configuration whose consistency spans lines.

## Resolve a disputed boundary with a patch

When review identifies independent outcomes, construct the proposed narrower
before/after versions. Compare a provider-first sequence with its later
adopter, or separate the relevant behavior branches. Run focused checks on the
actual versions. A hypothetical sentence about breakage is insufficient.

If both narrower steps have a useful coherent contract, promote them to
separate concerns and update successors. If they belong to one transition,
cite the concrete contract and experiment. Tests can prove a provider directly
without prematurely introducing its eventual UI or CLI consumer.

Do not enumerate every function as a speculative subconcern. Review concrete
independent outcomes visible in the patch. Straightforward small concerns need
a patch and relevant proof; a split experiment is required when the boundary
is disputed or inspection exposes distinct outcomes. Avoid hundreds of
identical assurance paragraphs asserting that every tiny helper is atomic.

## Proof must match the claim

Run checks in the historical snapshot with only its declared prerequisites.
Use the installed refine-history `verify-head-snapshot.py` helper with
`--repo` pointing to the disposable repository and `--ref` set to the after
commit. Always supply an explicit command. Its default Python compile check
does not verify JavaScript, behavior, or another project's runtime.

Choose focused unit, parser/decoder, CLI, build, or browser checks appropriate
to the changed contract. Syntax, module import, and test collection checks are
supplementary evidence, not behavior proof. A new provider may need a small
direct test before an adopter's test exists. For code whose repository
requires a subsequent test commit, execute a retained temporary proof harness
against the code snapshot or run an equivalent direct check. That harness may
contain the next commit's test assertions, but must use only code and
prerequisites present at the code snapshot. Record its path and invocation; do
not claim the later committed test file exists yet. Do not invent test names,
commands, environment variables, runners, or source anchors. If a necessary
proof does not exist, construct and execute it; retained project tests belong
with or immediately after the behavior they verify, per repository convention.
For a docs-only or mechanical change, record the relevant inspection instead
of creating an unnecessary test.

Resolve command executables and dependency setup for the isolated checkout. An
absolute Python interpreter may be a prerequisite; an installed copy of the
final package must not replace imports from the historical source. A
JavaScript checkout may require installation from its own lockfile. Record
setup separately from checks and prove that it does not alter tracked files.

Generate ignored assets using the scripts/configuration present at that
snapshot. Record asset versions, network needs, credentials, server startup,
and ports when applicable. Browser proof must exercise a real scenario with
those prerequisites met. Run browser checks for changes affecting browser
behavior or adding a scenario; do not run them mechanically for unrelated
Python or documentation changes. Missing prerequisites are blockers or proof
limits, never passing behavior evidence.

Logs belong to a specific snapshot and command. Failed experiments can be
retained as evidence, but accepted snapshots need successful relevant checks
or an explicit external blocker. The gates cannot claim success while required
proof is blocked. Reuse checks only when the snapshot and all relevant inputs
are unchanged. Later runtime verification still checks the actual commits.

## Review corrections are local

Record a review finding with the concern slug, disputed outcomes, proposed
patch or dependency correction, and missing evidence. For a failure:

1. Reconstruct the disputed patch and inspect the actual versions.
2. Correct that boundary or dependency. Rebuild snapshots whose before tree,
   shared file content, imports/calls, or prerequisites changed.
3. Update affected ownership, order, narrative, checks, and batch mappings.
4. Retain unaffected decisions and evidence. Run the complete structural and
   semantic gate before promoting the plan or continuing to the next phase.

A formatting/schema mistake needs a formatting/schema correction. A semantic
failure needs a patch correction. Neither is a reason to regenerate every
concern from prose. Do not silence a finding by renaming a bundled purpose.

If reviewers repeat the same objection after a correction, stop the abstract
replanning cycle: have the analyzer and reviewer inspect the same proposed
narrower patches and check output. Resolve the concrete discrepancy. If no
implementation can satisfy the contract with available prerequisites, report
that exact blocker; do not keep producing fresh untested plans.

The narrative and evolution ladder summarize the resulting snapshots. They are
review aids, not inputs from which imaginary code versions are inferred. Keep
them concise, with pointers to evidence for unusual boundaries.
