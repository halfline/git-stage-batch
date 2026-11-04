---
name: commit-staged-changes
description: Commit the current staged index as one project-compliant git commit. Use when the user has already staged the exact changes they want to commit and wants help inspecting that staged set, drafting a compliant message, and creating the commit. Do not use for splitting or staging unstaged changes.
user-invocable: true
disable-model-invocation: true
context: fork
when_to_use: Use when the user wants Claude Code to commit the currently staged index as one project-compliant commit. Examples: "commit what's staged", "commit the index", "make a commit from the staged changes", "write a message and commit the staged diff".
allowed-tools:
  - Read
  - Grep
  - Glob
  - LS
  - Agent(commit-message-drafter)
  - Bash(git *)
---

# Commit Staged Changes

Use this skill to turn the current **staged** index into one git commit with
project-compliant commit messaging.

## Installed resources and project guidance

Set `ASSET_ROOT` to the absolute `.claude` directory containing this installed
skill, two levels above its skill directory. Resolve it from the loaded
`SKILL.md` path, whether installed in the repository or the user's home.
Resolve `references/` and `scripts/` paths relative to this skill directory.
Keep the working directory in the target repository, quote helper paths in
shell commands, and pass `ASSET_ROOT` to workers that use companion resources.

Before drafting, search the project tree for `CONTRIBUTING.md`, excluding
installed assistant assets. Check the repository root, `.github/`, `docs/`,
and guidance scoped to the changed paths. Read the applicable project guide.
Only if the project has no contribution guide of its own, read
`$ASSET_ROOT/internal/CONTRIBUTING.md`. Other project instructions and commit
hooks still take precedence over bundled defaults. Pass the selected guide's
path and applicable rules to any drafting worker.

## Usage

```text
/commit-staged-changes
```

This skill is autonomous and non-interactive. Inspect the staged set, confirm
that it is coherent enough to commit as one change, write a compliant commit
message, create the commit, and then stop without asking the user to review
the draft first.

This skill only handles content that is already staged. Do not stage unstaged
changes, do not unstage anything, and do not rearrange the index into a
different split. Leave unstaged work exactly as you found it.

## Git Command Concurrency

Always pass `--no-optional-locks` to read-only git commands such as
`git status`, `git diff`, `git log`, and `git show`. Without this flag,
git refreshes cached filesystem metadata in the index, which requires
`.git/index.lock`. When Claude Code runs multiple read-only git commands
in parallel, which it does by default for concurrency-safe tools, two
stat-refreshing commands race for that lock and one fails.

Claude Code's own internal git commands already use this flag, but
commands run through BashTool do not get it injected automatically.

```bash
# correct
git --no-optional-locks status --short
git --no-optional-locks diff --cached --stat
git --no-optional-locks diff --cached
git --no-optional-locks log --pretty=oneline -- path/to/file

# incorrect: will race when parallelized
git status --short
git diff --cached
```

This applies to every `git` invocation in the skill that does not need to
modify the index. Commands that intentionally write to the index, such
as `git commit`, `git add`, or `git apply --cached`, must not use this
flag.

## Core Workflow

1. Inspect repository state with `git --no-optional-locks status --short`.
2. Check whether the index contains staged changes.
3. If there are no staged changes, stop and tell the user there is nothing to
   commit.
4. Read repository-specific commit guidance before drafting messages:
   - `CONTRIBUTING.md` when present
   - `.git/hooks/commit-msg` when present
5. Inspect the staged set with:
   - `git --no-optional-locks diff --cached --stat`
   - `git --no-optional-locks diff --cached`
6. Treat unstaged changes as context only. They are not part of this commit
   and must remain untouched.
7. Decide whether the staged set is coherent as one commit.
8. If the staged set clearly contains multiple independent concerns, stop and
   explain that the current index should be split before committing. Do not
   repair the split yourself under this skill.
9. If the staged set is coherent, use `Agent(commit-message-drafter)` after
   the staged diff is complete if a fresh-context draft would help.
10. Create the commit from the staged set only.
11. If the commit fails because the message format is wrong, fix the message
    and retry.
12. If the commit fails because the staged path set or staged content violates
    repository policy, stop and explain the blocker instead of restaging.

## Decision Rules

- The staged set must stand on its own as one commit.
- Shared directory, subsystem, or command surface is not enough to make two
  changes one concern.
- If the staged set mixes multiple concrete implementations, bug fixes, or
  independently testable behaviors, stop and tell the user the index should be
  split before committing.
- Do not broaden the story of the commit just because the staged set is mixed.
- Do not silently commit an obviously non-atomic index just to finish.

## Commit Message Drafting

When the current commit is fully staged, you may use the shared
`commit-message-drafter` agent for a fresh-context draft.

- Spawn it only after the staged diff for the current commit is complete.
- Tell it not to stage, edit, or commit anything.
- Give it a self-contained briefing that includes:
  - the relevant state before the patch, with supporting code locations
  - whether this is a single commit or part of a series
  - the exact change, with supporting source evidence
  - the supplied motivation, observations, assessments, and relevant history,
    retaining their degree of certainty and who made relevant choices; identify
    supplied context separately from independently verified source facts;
    keep your inferences separate from the caller's actual claims
  - whether the patch adds machinery or makes behavior run in a caller
  - only the dependencies needed to explain the patch's design or scope
  - the series goal as background, without assigning a role to its position
  - the repository-specific message rules already discovered
  - any preferred prefixes established by history
  - the exact files staged for this commit
- Tell it to inspect only what it needs, typically:
  - `git --no-optional-locks diff --cached --stat`
  - `git --no-optional-locks diff --cached`
  - `git --no-optional-locks log --pretty=oneline -- <path>` for representative staged paths
  - `CONTRIBUTING.md` when present
  - `.git/hooks/commit-msg` when present

Review the draft in the main skill before committing. If it no longer matches
the staged diff or repository rules, fix it in the main skill rather than
pushing the problem back to the user.

Before committing, edit the draft into one alternative using the same
source evidence and supplied context. Keep the first body sentence as a
longer, non-imperative version of the summary. Preserve the intended
behavior, supported judgments, actual uncertainty, and necessary scope.

Edit the summary and its expanded first sentence too when their wording
hides the action in an abstract label. Keep the expanded sentence first,
and use it to state the actual change in ordinary words.

After that opening, edit as if a colleague has asked why we need the
change. Put the action or need in ordinary verbs. Let a short judgment
stand on its own. Remove a sentence that merely labels the change or
repeats a consequence the surrounding prose already makes clear. Keep inspection notes in CHECKS.

Compare the reasoning and how naturally it is expressed. A wording
improvement may keep every fact and causal step unchanged; it does not
need a new reason or extra detail to earn its place. Preserve useful
causal links instead of replacing them with a list of operations. Check
local references from the prose alone. Unpacking an unexplained reference
is a real improvement even when it takes more words. A descriptive label
or a related word used earlier does not establish its meaning. Prefer
stating the condition and action when that saves the reader from having
to reconstruct the relationship. Keep the original if the alternative is
less natural, loses needed reasoning,
or offers no improvement. Let difficult interactions take room and direct
reasons stay brief. Length, contractions and first person do not decide
which version is better.

Repeat the edit-and-compare step using the best version so far. Make at
most three comparisons in total, including the first. Stop after two
consecutive comparisons offer no clear improvement. Keep wording that
already works; an extra comparison does not require accepting an edit.
Fix factual errors and repository-rule violations before committing,
regardless of that limit.

Write and choose in the main skill's existing review. The comparison is
internal; use the selected message for the commit.

Verify the final wording with the accuracy check in the writing guidance
below before committing. Repeat that check after any further wording edits.

## Commit Message Shape

Follow repository-specific rules first. If no repository guidance overrides
this, use:

```text
prefix: Summary under 72 chars

[A plain-language statement of the changed behavior, followed by the
concrete old behavior and how the change addresses it. Use declarative
narrative present tense and the explanation the reader needs.]
```

### Explain the Change

The first body sentence is a longer, non-imperative version of the summary
line. State the same change in plain language as an indicative, declarative
sentence, with enough detail to make the outcome clear. Keep that opening
before the background and reasoning.

Explicitly attribute newly introduced behavior to the current commit, using
`This commit ...` or an equally clear construction. Describe existing
behavior in present tense from the state before the patch. Leading with the
outcome does not change that temporal viewpoint. For example, `This commit
adds pattern matching to the command` identifies the patch as the cause;
`The command now accepts patterns` can blur the change with the status quo.

Use straight quotes and apostrophes in commit message prose. Avoid em
dashes. Use commas, parentheses, or separate sentences where needed.

In prose, prefer periods to semicolons.

After the opening, explain the decision as you would to another
maintainer who has not investigated the problem. Use ordinary verbs and
familiar terms, with contractions when they fit. Let the reader follow
the thought: a reasonable assumption, the catch, and the consequence that
makes the change worth doing. Include those turns when the reasoning
needs them; a direct fix may need only its direct reason.

Match the firmness of each claim to its basis. State a demonstrated
failure, unnecessary restriction, or supported cost plainly. Preserve
uncertainty about people's behavior, predictions, and design judgments
where the evidence leaves room. A message can explain the code confidently
and qualify an inference about its users in the next sentence.

State established behavior directly, with the condition that matters.
Use "can" or "may" for meaningful capability, contingency, or uncertainty,
not automatic precision. Check changed implications against the evidence.

Write decisions from inside the project. When relevant context explains
who made an earlier choice and why, retain that ownership and reasoning.
"We" can describe a shared decision or need; a component name is often
clearer for an operation. First person does not establish personal
experience or project history that the evidence does not supply.

Caller-supplied motivation, observations, assessments, and relevant history
can explain the change even when absent from the diff. Carry their meaning
and degree of certainty through the drafting handoff. In the message,
combine clarifications that explain the same fact. Include history when
it helps explain the present decision. Keep provenance and
verification limits outside the commit message. Report contradictions
with source there and avoid relying on a contradicted account as fact.
Never claim independent verification merely because context was supplied.

Supplied context establishes meaning, not a list of points to cover. Treat
a wording clarification as a constraint on the relevant claim. Do not turn
it into a separate explanation of a hypothetical misunderstanding.

Spend words where the reader's expectation could go wrong. Explain how
the operation's calls or events fit together before introducing internal
paths or states. Prefer concrete nouns such as "cleanup code" over labels
such as "shared cleanup". For an unnamed branch, state its condition and
action directly. Use a local name as shorthand once its meaning is clear.
A timing or ownership problem may need a patient walkthrough of why the
apparent fix fails; an unnecessary requirement may take only a sentence
to dismiss.
Follow the chosen mechanism through to its practical consequence so the
reader understands why it is worth using. Give a judgment or a turn in
the argument its own sentence or paragraph
when that helps it land. Keep a brief reminder when it connects the next
step. Once the reason, decision, and necessary scope are clear, stop;
additional implementation facts do not create a need for more prose.

The summary should make the difference from the status quo apparent, rather
than merely name a desirable end state. Choose wording from the actual
before-and-after behavior. For example, `Align statistics with the visible
sensor crop` implies that the measured and displayed regions differ;
`Process only the selected files` implies that unselected files are currently
included. These illustrate how meaning supplies the contrast, not a fixed
vocabulary or a list of required verbs. Choose the words that best fit the
specific patch. Use a qualifier such as `only` when the restriction is real;
do not imply exact exclusion if alignment or rounding can include extra data.

The expanded opening should state that same concrete action in ordinary
words, without cataloguing internal operations or unchanged preconditions.

Select technical details for the understanding they give the reader.
Keep a detail when it explains the problem, why the change works, a
design choice, or a meaningful limit. A new internal API can be the
outcome of a preparatory patch; explain its purpose without cataloguing
all its capabilities. When remaining work matters to the explanation,
say what still needs to be done. Use active verbs for those actions instead
of listing missing components or call sites.

For tone, given repeated reads of an unchanged file, "We read the same
file for every row. Once is enough" explains the judgment more naturally
than "Repeated file reads represent avoidable overhead". When a field is
not yet read or forwarded, "We still need to read the field and pass its
value to the worker" puts the remaining actions in active verbs. "The reader
and value forwarding still need to be added" leaves them in nouns and
passive prose. When work remains, use a person or component as the subject
and say what they still need to do. These illustrate ordinary explanation,
not phrases to copy or a requirement to use first person.

Keep verification notes outside the commit message. A condition belongs
in the message when leaving it out would obscure the reasoning or
materially misstate the claim. The body need not demonstrate every fact
checked during inspection. Scope the claim accurately and give its
explanation room.

Make each necessary scope limit qualify the claim it belongs to when
that reads plainly. Once a clarification is expressed in that claim,
remove a sentence that only restates the same distinction. Keep a separate
limit when it explains a stopping point or another consequence. Do not
append an inventory of operations or unchanged behavior simply to account
for the patch.

Before accepting the message, read it on its own as an explanation to
someone who has not inspected the diff. Consider the question each
paragraph answers at that point in the explanation. Keep the details
that answer that question; move answers to separate questions raised
only by inspecting the implementation into review notes outside the message.

Revise in both directions: restore an intermediate step the reader
needs to understand the consequence, and remove facts whose main purpose
is to demonstrate thorough inspection. A feature's presence in the patch
supports its accuracy, but does not by itself explain why the reader
needs it. Keep the maintainer's supported judgment and the connection
between ideas when shortening the message.

After drafting or rewording, verify the meaning of the final sentences
against the selected patch, relevant prior code, and supplied context.
Check the conditions, timing and effects the message states or implies.
A simpler phrase can introduce a new claim; an operation's name does not establish
its effect. Inspect the relevant implementation when that distinction
matters. Narrow or remove unsupported implications and report any
remaining uncertainty outside the message. Correct contradictions in the
affected claim, then remove any caveat or reassurance that only repeats
its corrected meaning. Repeat the accuracy check after further edits.
Keep code evidence, supplied context, and inference distinct in review
notes.

### Message Rules

- Use a short lowercase prefix such as `cli:`, `docs:`, `tests:`, `build:`,
  `state:`, or another prefix established by history.
- Keep the summary line under 72 characters. Prefer a concrete subsystem prefix
  when prefixes are free-form; generic `fix:` and `refactor:` labels usually add
  little signal. Conventional Commits and other declared formats take precedence.
- State, problem, and solution are questions to answer, not paragraphs to fill.
  Combine them when separating them repeats the same fact. A mechanical change
  may need one sentence; a one-line correctness fix may need several paragraphs.
- Use imperative voice only in the summary. Write every body sentence as an
  indicative, declarative statement in narrative present tense.
- Use a light temporal cue only where it clarifies time-dependent behavior.
  Timeless background or a lasting contract can stand unqualified; naming
  the project or component may already establish the context. If the
  ambiguity is in the problem paragraph, anchor that claim instead. Let
  `This commit ...` mark the transition, without implying that every
  background fact changes afterward. Vary naturally; avoid repeated cues.
- Use `already` for a useful contrast, not merely because a capability
  exists before the patch. If a recent change established the relevant
  state, recount it in past tense and attribute it to earlier work:
  `Recent commits moved ...`. Describing a past change differs from
  describing the existing state in present tense. Avoid `now` alone,
  which can imply the current commit made that change. Verify claims
  against the previous commit, not later work.
- In message prose, prefer `commit` and `previous commit` to `revision` and
  `parent commit`.
- Explain the actual reason at the smallest scope that makes it understandable.
  Internal simplification can be sufficient. Do not invent reported failures,
  benchmarks, testing history, or rejected alternatives.
- Prefer `This commit ...` to explain precisely what changes wherever it fits
  naturally. It does not require a separate paragraph or a repeated subject.
- Before claiming that behavior begins, verify that this patch makes it run.
  A helper that callers will adopt separately is a legitimate outcome.
- Keep present tense for the pre-change state and for the change itself;
  describe work in later commits in future tense.
- A future use for a capability does not establish follow-up commits.
  Mention later commits only when the supplied series or plan establishes
  them; otherwise explain the purpose without promising a sequence.
- Make references to future work in the series explicit: `in a later commit`,
  `later commits will ...`, or `the final commit will ...`, not bare `later`.
- Match the state after the previous commit without recapping unrelated
  earlier work.
- A commit explains its own patch. Its position creates no obligation to
  introduce, summarize, or conclude the series. Put a series-wide overview or
  recap in the review request while retaining the patch's own rationale.
- Write for a competent developer who does not know this part of the codebase.
  Explain the local roles and specialized terms needed for the change.
- Keep explanations within the commit history. Do not refer to outside
  development context such as "the plan" or "review results". Explain the
  motivation directly, using only context available at that point in the
  series.
- Expect a drive-by reader to be new to the codebase but likely to read the
  series together in order. Explain shared context where it first matters;
  later commits may rely on it. Keep useful repetition as a shorter reminder,
  adding new detail where relevant. A standalone commit needs its own context.
- Preserve references that explain the current step's role, a design choice,
  a dependency, or deliberately unfinished scope. Explain the connection,
  not just the next commit's subject.
- For each cross-commit reference, identify the fact about this patch it
  explains. Remove it if it only says what happens elsewhere in the series.
  A useful reference can appear wherever it belongs in the explanation.
- Verify retained references against actual patches and distinguish future
  work from behavior established here. Omit unrelated recaps and next-item
  announcements.
- Do not use `this` for anything other than `this commit`.
- Ground assessments in concrete behavior. A supported judgment can help
  explain a design choice.

## Safety Checks

- Do not use `git commit -a`.
- Do not use `git add`, `git reset`, or `git restore --staged` under this
  skill.
- Do not edit tracked files just to make the staged set easier to explain.
- Do not commit pre-existing unstaged changes.
- If the repository is not a git repository, stop and report that clearly.

## Completion

After committing, report:

- the commit hash
- the subject line
- whether unstaged changes were left in place
