# git-stage-batch

**Writing code is messy. Git history doesn't have to be.**

During development we experiment, refactor, backtrack, and fix mistakes. If every step ends up as a commit, the history becomes noise.

A curated history turns that process into a clear sequence of logical changes. Each commit captures one idea, and the message explains why it exists.

This clarity assists contributors explore the codebase, maintainers review changes, and your future self try to understand how the system evolved.

**git-stage-batch** helps you build that history incrementally by letting you stage changes hunk-by-hunk or line-by-line, shaping commits around meaning instead of the order the edits happened.

<div class="grid cards" markdown>

-   :material-console-line:{ .lg .middle } __Command-Based Workflow__

    ---

    Perfect for automation and AI coding assistants. Chain commands together for precise control.

    [:octicons-arrow-right-24: Quick Start](#quick-start)

-   :material-hand-pointing-up:{ .lg .middle } __Interactive Mode__

    ---

    Beginner-friendly prompts similar to `git add -p` with single-letter shortcuts.

    [:octicons-arrow-right-24: Interactive Guide](interactive.md)

-   :material-code-braces:{ .lg .middle } __Line-Level Control__

    ---

    Stage specific lines within a hunk for maximum granularity. Perfect for separating mixed changes.

    [:octicons-arrow-right-24: Commands Reference](commands.md)

-   :material-robot:{ .lg .middle } __Machine-Readable Output__

    ---

    `--porcelain` flag for scripting. Integrate into your tools and workflows.

    [:octicons-arrow-right-24: See Examples](examples.md)

</div>

## See it in Action

![git-stage-batch demo](assets/demo.gif)

*Creating atomic commits: bug fix, validation feature, and build artifact exclusion*

## Why git-stage-batch?

Similar to `git add -p` but **more granular and flexible**:

- ✅ **Command-based mode** - Perfect for automation and AI assistants
- ✅ **Interactive mode** - Beginner-friendly prompts like `git add -p`
- ✅ **Line-by-line staging** - Stage specific lines within a hunk
- ✅ **State persistence** - Resume staging across multiple invocations
- ✅ **Colored output** - Clear visual distinction in your terminal
- ✅ **File operations** - Stage/skip entire files at once
- ✅ **No dependencies** - Pure Python standard library

## Quick Start

### Installation

=== "uv (recommended)"

    ```bash
    uv tool install git-stage-batch
    ```

=== "pipx"

    ```bash
    pipx install git-stage-batch
    ```

=== "pip"

    ```bash
    pip install git-stage-batch
    ```

=== "meson"

    ```
    # Clone and build
    ❯ git clone https://github.com/halfline/git-stage-batch.git
    ❯ cd git-stage-batch
    ❯ meson setup build
    ❯ meson compile -C build

    # Install to system
    ❯ sudo meson install -C build
    ```

### Basic Usage

```bash
# Start reviewing hunks
git-stage-batch start

# Include the current hunk (stage it)
git-stage-batch include
# Or use the short alias:
git-stage-batch i

# Skip it for now
git-stage-batch skip    # or: s

# Discard it (remove from working tree)
git-stage-batch discard # or: d

# For fine-grained control, stage specific lines
git-stage-batch include-line 1,3,5-7  # or: il 1,3,5-7
git-stage-batch skip-line 2,4         # or: sl 2,4

# Check status
git-stage-batch status  # or: st

# Start fresh after committing
git-stage-batch again   # or: a
```

## Interactive Mode

For a workflow similar to `git add -p`:

```bash
git-stage-batch --interactive
```

Interactive mode presents hunks one at a time with beginner-friendly prompts and single-letter shortcuts.

[Learn more about interactive mode →](interactive.md){ .md-button }

## Example Workflow

```bash
# You have changes in multiple files
$ git status
modified:   auth.py
modified:   config.py

# Start staging process
$ git-stage-batch start
auth.py :: @@ -10,5 +10,5 @@
[#1] - old_hash_function()
[#2] + new_hash_function()
      validate_user()

# Include this for first commit
$ git-stage-batch i
config.py :: @@ -20,3 +20,4 @@
[#1] + DEBUG = True
      TIMEOUT = 30

# This debug flag shouldn't be committed, skip it
$ git-stage-batch s
No pending hunks.

# Create first commit
$ git commit -m "auth: Upgrade to new hash function"

# Go through skipped hunks for next commit
$ git-stage-batch a
config.py :: @@ -20,3 +20,4 @@
[#1] + DEBUG = True
      TIMEOUT = 30

# Discard this debug line instead
$ git-stage-batch d
No pending hunks.

# Working tree is now clean
$ git status
nothing to commit, working tree clean
```

## Features

### Hunk-by-Hunk Staging

Review and stage individual hunks one at a time. Each hunk shows changed lines with IDs for easy reference.

### Line-by-Line Staging

Stage specific lines within a hunk:

```bash
git-stage-batch include-line 1,3,5-7
```

Perfect for separating orthogonal changes that ended up in the same hunk.

### Colored Output

Automatic color support with TTY detection:

- 🟢 Green for additions
- 🔴 Red for deletions
- 🔵 Cyan for headers
- ⚫ Gray line numbers for easy scanning

### State Persistence

Track processed/skipped hunks across multiple command invocations. Resume where you left off.

### Stale State Detection

Automatically detects and clears cached state when files are committed or modified externally. No more misleading status!

## FAQ

### Is this rewriting Git history?

No.

git-stage-batch is intended for organizing draft patch sets before they are committed or shared. It helps you turn a messy working tree into a clean sequence of logical commits.

It does not rewrite existing commits, and it is not meant to modify the history of shared or protected branches.

Think of it as helping you prepare commits before they become part of history, not changing history afterward.

### When should I use this?

Use it while preparing commits for a branch you are working on locally.

A typical workflow looks like:

```
edit code
edit more code
experiment
fix mistakes
```

Then run:

```
❯ git-stage-batch start
```

to turn those edits into a clean set of commits.

Once the commits are ready, you can push or open a pull request as usual.

### Why not just use git add -p?

`git add -p` is great for staging individual changes, but it is designed for single-pass staging.

git-stage-batch is designed for multi-pass commit curation:

```
stage changes
make a commit
run again
stage the next logical change
repeat
```

This makes it easier to organize a large working tree into a series of clean commits.

### Is this safe for protected branches?

Yes — because you should not use it there.

This tool is meant for local development branches before merging.

Once commits are pushed or merged into protected branches, standard Git practices apply and history should normally remain stable.

### Is this similar to git rebase -i?

It solves a related problem but at a different stage.

- `git rebase -i` reorganizes existing commits
- `git-stage-batch` helps you create better commits in the first place

Many developers will still use `rebase -i` occasionally, but with curated commits it becomes much less necessary.

### Why curate Git history at all?

Because Git history is read by people.

A raw commit log is a transcript of development: experiments, mistakes, and partial fixes.

A curated history is documentation of how the system evolved. It is far easier for contributors, reviewers, and your future self to understand.

## Next Steps

<div class="grid cards" markdown>

-   :material-rocket-launch:{ .lg .middle } __Get Started__

    ---

    Install and run your first staging session in minutes.

    [:octicons-arrow-right-24: Installation Guide](installation.md)

-   :material-book-open-variant:{ .lg .middle } __Learn the Commands__

    ---

    Complete reference of all commands and options.

    [:octicons-arrow-right-24: Commands Reference](commands.md)

-   :material-code-braces:{ .lg .middle } __See Examples__

    ---

    Common workflows and use cases.

    [:octicons-arrow-right-24: Examples](examples.md)

-   :material-robot:{ .lg .middle } __Configure AI Assistants__

    ---

    Set up Claude, Cursor, or other AI coding assistants.

    [:octicons-arrow-right-24: AI Assistant Guide](ai-assistants.md)

</div>
