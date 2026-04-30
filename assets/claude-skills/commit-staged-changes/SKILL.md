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
  - the current commit's one-clause purpose
  - whether this is a single commit or part of a series
  - whether this is the final commit in the series
  - the repository-specific message rules already discovered
  - any preferred prefixes established by history
  - the exact files staged for this commit
- Tell it to inspect only what it needs, typically:
  - `git diff --cached --stat`
  - `git diff --cached`
  - `git log --pretty=oneline -- <path>` for representative staged paths
  - `CONTRIBUTING.md` when present
  - `.git/hooks/commit-msg` when present

Review the draft in the main skill before committing. If it no longer matches
the staged diff or repository rules, fix it in the main skill rather than
pushing the problem back to the user.

## Commit Message Shape

Follow repository-specific rules first. If no repository guidance overrides
this, use:

```text
prefix: Summary under 72 chars

[First paragraph: the program's current state.]

[Second paragraph: the underlying problem.]

This commit [addresses|mitigates|resolves] that [problem] by
[precise description of what this commit changes].

[Optional fourth paragraph: what comes next.]
```

### Message Rules

- Use a short lowercase prefix such as `cli:`, `docs:`, `tests:`, `build:`,
  `state:`, or another prefix established by history.
- Keep the summary line under 72 characters.
- The first paragraph describes the project's state immediately before this
  commit is applied.
- The second paragraph explains the real underlying problem from the right
  perspective.
- The third paragraph starts with `This commit` and precisely explains what
  this commit changes.
- Add a fourth paragraph only when this commit is part of a larger near-term
  series and the next step matters.
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
