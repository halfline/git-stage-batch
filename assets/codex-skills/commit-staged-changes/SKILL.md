---
name: commit-staged-changes
description: Commit the current staged index as one project-compliant git commit. Use when the user has already staged the exact changes they want to commit and wants help inspecting that staged set, drafting a compliant message, and creating the commit. Do not use for splitting or staging unstaged changes.
metadata:
  short-description: Commit staged changes
compatibility: Designed for Codex with git available.
---

# Commit Staged Changes

Use this skill to turn the current **staged** index into one git commit with
project-compliant commit messaging.

This skill is autonomous and non-interactive. Inspect the staged set, confirm
that it is coherent enough to commit as one change, write a compliant commit
message, create the commit, and then stop without asking the user to review
the draft first.

This skill only handles content that is already staged. Do not stage unstaged
changes, do not unstage anything, and do not rearrange the index into a
different split. Leave unstaged work exactly as you found it.

## Core Workflow

1. Inspect repository state with `git status --short`.
2. Check whether the index contains staged changes.
3. If there are no staged changes, stop and tell the user there is nothing to
   commit.
4. Read repository-specific commit guidance before drafting messages:
   - `CONTRIBUTING.md` when present
   - `.git/hooks/commit-msg` when present
5. Inspect the staged set with:
   - `git diff --cached --stat`
   - `git diff --cached`
6. Treat unstaged changes as context only. They are not part of this commit
   and must remain untouched.
7. Decide whether the staged set is coherent as one commit.
8. If the staged set clearly contains multiple independent concerns, stop and
   explain that the current index should be split before committing. Do not
   repair the split yourself under this skill.
9. If the staged set is coherent, draft the commit message.
10. Prefer a fresh-context subagent for commit-message drafting after the
    staged set is complete. Use the shared `commit-message-drafter`
    constraints below as the briefing template rather than keeping message
    drafting in the same long-running context when the session involved
    substantial staging or series analysis.
11. Create the commit from the staged set only.
12. If the commit fails because the message format is wrong, fix the message
    and retry.
13. If the commit fails because the staged path set or staged content violates
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

## Fresh-Context Message Drafting

For Codex, the equivalent of Claude's dedicated message-drafter agent is a
read-only subagent briefed with the same constraints as the shared
`commit-message-drafter` asset. Prefer that pattern by default once the staged
diff is complete.

If you spawn a subagent for message drafting:

1. Spawn it only after the staged diff for the current commit is complete.
2. Tell it not to stage, edit, or commit anything.
3. Give it a self-contained briefing that includes:
   - the current commit's one-clause purpose
   - whether this is a single commit or part of a series
   - the series' overall goal and the current commit's contribution
   - whether this is the opening commit in the series
   - whether this is the final commit in the series
   - whether this is the penultimate commit in the series, when known
   - the repository-specific message rules already discovered
   - any preferred prefixes established by history
   - the exact files staged for this commit
4. Tell it to inspect only what it needs, typically:
   - `git diff --cached --stat`
   - `git diff --cached`
   - `git log --pretty=oneline -- <path>` for representative staged paths
   - `CONTRIBUTING.md` when present
   - `.git/hooks/commit-msg` when present
5. Require it to return:
   - one proposed commit message
   - a short checklist confirming prefix, paragraph count, series framing,
     and the relevance of any fourth-paragraph connection
   - any specific uncertainty if the staged diff does not justify a confident
     draft

The shared `commit-message-drafter` asset is the canonical source for those
constraints. Do not activate another skill solely for message isolation;
spawn a subagent and inline the needed briefing instead.

Review the draft in the main skill before committing. If it no longer matches
the staged diff or repository rules, fix it in the main skill rather than
pushing the problem back to the user.

## Commit Message Shape

Follow repository-specific rules first. If no repository guidance overrides
this, use:

```text
prefix: Summary under 72 chars

[First paragraph: the relevant state, using present tense for state
descriptions. If recent work established that state, briefly recount the
change in past tense and loosely when it happened. Leave timeless
background unqualified; use "already" only for a useful contrast.]

[Second paragraph: the underlying problem.]

This commit [addresses|mitigates|resolves] that [problem] by
[precise description of what this commit changes].

[Optional fourth paragraph: a useful connection explaining this patch's
role in the series, a dependency, or deliberately unfinished scope.]
```

### Message Rules

- Use a short lowercase prefix such as `cli:`, `docs:`, `tests:`, `build:`,
  `state:`, or another prefix established by history.
- Keep the summary line under 72 characters.
- The first paragraph describes the project's state immediately before this
  commit is applied.
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
- The second paragraph explains the real underlying problem from the right
  perspective.
- The third paragraph starts with `This commit` and precisely explains what
  this commit changes.
- Keep present tense for the pre-change state and for the change itself;
  describe work in later commits in future tense.
- Make references to future work in the series explicit: `in a later commit`,
  `later commits will ...`, or `the final commit will ...`, not bare `later`.
- Match the state after the previous commit without recapping unrelated
  earlier work.
- Keep the series' goal, rationale, and useful connections in the history.
  The opening commit speaks for the series as well as its own patch; the
  final commit closes the story with the outcome actually achieved.
- Assume the reader may not know the underlying technology. Explain
  unfamiliar technology and industry acronyms in plain language, defining
  terms before using them. Prefer a fuller explanation when shorthand would
  make the reader decode the meaning.
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
- Keep a useful segue as a distinct fourth paragraph. Omit it when removing
  it makes the series' progression no harder to understand; do not fold it
  into the first three paragraphs or repeat their explanation.
- Verify retained references against actual patches and distinguish future
  work from behavior established here. Omit unrelated recaps and next-item
  announcements.
- Do not use `this` for anything other than `this commit`.
- Prefer concrete limitations over vague praise.

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
