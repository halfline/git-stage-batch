<div class="hero-wrapper" style="position: relative; padding: 2em 0;">
<div class="hero-mark" style="font-family: monospace; color: #00ff41; text-shadow: 0 0 15px #00ff41, 0 0 25px rgba(0, 255, 65, 0.4); line-height: 1.2; margin: 0 0 0.5em; text-align: center;">
<pre style="background: transparent; border: none; box-shadow: none; text-align: left; display: inline-block; font-size: 1.05em;">
   ┌─────────────────┐
   │ git-stage-batch │
   └─────────────────┘

       o───o
      /
  o───o───o

 stage patches in batches
</pre>
</div>

<div class="hero-tagline" style="font-size: 1.5em; font-weight: 600; line-height: 1.4; margin: 1.5em 0 1.5em; text-align: center; color: var(--hacker-cyan); text-shadow: 0 0 8px rgba(0, 240, 255, 0.6), 0 0 16px rgba(0, 240, 255, 0.3);">
Writing code is messy.<br>
Git history doesn't have to be.
</div>
<div class="mobile-github-widget">
  <a href="https://github.com/halfline/git-stage-batch" title="Go to repository">
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 496 512" fill="currentColor"><path d="M165.9 397.4c0 2-2.3 3.6-5.2 3.6-3.3.3-5.6-1.3-5.6-3.6 0-2 2.3-3.6 5.2-3.6 3-.3 5.6 1.3 5.6 3.6zm-31.1-4.5c-.7 2 1.3 4.3 4.3 4.9 2.6 1 5.6 0 6.2-2s-1.3-4.3-4.3-5.2c-2.6-.7-5.5.3-6.2 2.3zm44.2-1.7c-2.9.7-4.9 2.6-4.6 4.9.3 2 2.9 3.3 5.9 2.6 2.9-.7 4.9-2.6 4.6-4.6-.3-1.9-3-3.2-5.9-2.9zM244.8 8C106.1 8 0 113.3 0 252c0 110.9 69.8 205.8 169.5 239.2 12.8 2.3 17.3-5.6 17.3-12.1 0-6.2-.3-40.4-.3-61.4 0 0-70 15-84.7-29.8 0 0-11.4-29.1-27.8-36.6 0 0-22.9-15.7 1.6-15.4 0 0 24.9 2 38.6 25.8 21.9 38.6 58.6 27.5 72.9 20.9 2.3-16 8.8-27.1 16-33.7-55.9-6.2-112.3-14.3-112.3-110.5 0-27.5 7.6-41.3 23.6-58.9-2.6-6.5-11.1-33.3 2.6-67.9 20.9-6.5 69 27 69 27 20-5.6 41.5-8.5 62.8-8.5s42.8 2.9 62.8 8.5c0 0 48.1-33.6 69-27 13.7 34.7 5.2 61.4 2.6 67.9 16 17.7 25.8 31.5 25.8 58.9 0 96.5-58.9 104.2-114.8 110.5 9.2 7.9 17 22.9 17 46.4 0 33.7-.3 75.4-.3 83.6 0 6.5 4.6 14.4 17.3 12.1C428.2 457.8 496 362.9 496 252 496 113.3 383.5 8 244.8 8z"/></svg>
    <span>halfline/git-stage-batch</span>
  </a>
</div>
</div>

<div class="hero-intro" style="font-size: 1.05em; line-height: 1.65; max-width: 42em; margin: 0 auto 1em;">

During development we experiment, refactor, backtrack, and fix mistakes. If every step ends up as a commit, the history becomes noise.

A curated history turns that process into a clear sequence of logical changes. Each commit captures one idea, and the message explains why it exists.

This clarity assists contributors explore the codebase, maintainers review changes, and your future self try to understand how the system evolved.

</div>

<div style="font-size: 1.1em; font-weight: 500; max-width: 42em; margin: 0 auto 2em;">
<strong>git-stage-batch</strong> helps you build that history incrementally by letting you stage changes hunk-by-hunk or line-by-line, shaping commits around meaning instead of the order the edits happened.
</div>

<div style="text-align: center; margin: 2em 0 3em;">
  <a href="#quick-start" class="md-button md-button--primary" style="font-size: 1.1em; padding: 0.7em 2em; box-shadow: 0 0 20px rgba(0, 240, 255, 0.4);">
    Get Started
  </a>
</div>

<div style="text-align: center; margin: 3em 0;">
  <img src="assets/batch-of-patches.png" alt="Batch of patches - hacker preparing atomic commits" style="max-width: 90%; border-radius: 8px; box-shadow: 0 0 30px rgba(0, 240, 255, 0.4);" />
</div>

<div style="text-align: center; margin: 2em 0;">
  <audio controls class="podcast-player" style="width: 100%; max-width: 600px; border-radius: 8px;">
    <source src="https://github.com/halfline/git-stage-batch/releases/download/v0.5.0/podcast.m4a" type="audio/mp4">
    Your browser does not support the audio element.
  </audio>
  <p style="margin-top: 0.5em; font-size: 0.85em; opacity: 0.7;">
    🎧 git-stage-batch featured on the Deep Dive podcast!
  </p>
</div>

<div class="hero-separator" style="height: 1px; background: linear-gradient(90deg, transparent, rgba(0, 240, 255, 0.3) 50%, transparent); margin: 4em auto; max-width: 60%;"></div>

<div class="grid cards" markdown>

-   :material-console-line:{ .lg .middle } __Command-Based Workflow__

    ---

    Perfect for automation and AI coding assistants. Chain commands together for precise control.

    [:octicons-arrow-right-24: Quick Start](#quick-start)

-   :material-code-braces:{ .lg .middle } __Line-Level Control__

    ---

    Stage specific lines within a hunk for maximum granularity. Perfect for separating mixed changes.

    [:octicons-arrow-right-24: Commands Reference](commands.md)

-   :material-robot:{ .lg .middle } __Machine-Readable Output__

    ---

    `--porcelain` flag for scripting. Integrate into your tools and workflows.

    [:octicons-arrow-right-24: See Examples](examples.md)

-   :material-keyboard:{ .lg .middle } __Interactive Mode__

    ---

    Menu-driven hunk-by-hunk workflow inspired by git add -p. Review and stage changes in a continuous session.

    [:octicons-arrow-right-24: Interactive Mode](interactive.md)

-   :material-package-variant:{ .lg .middle } __Named Batches__

    ---

    Defer and label changes for later processing. Advanced patch-series organization for complex workflows.

    [:octicons-arrow-right-24: Batch Operations](batches.md)

</div>

## See it in Action

![git-stage-batch demo](https://github.com/halfline/git-stage-batch/releases/download/v0.5.0/demo.gif)

*Creating atomic commits: bug fix, validation feature, and build artifact exclusion*

## Why git-stage-batch?

Similar to `git add -p` but **more granular and flexible**:

- ✅ **Interactive mode** - Continuous hunk-by-hunk workflow with menus
- ✅ **Command-based mode** - Perfect for automation and AI assistants
- ✅ **Line-by-line staging** - Stage specific lines within a hunk
- ✅ **State persistence** - Resume staging across multiple invocations
- ✅ **Colored output** - Clear visual distinction in your terminal
- ✅ **File operations** - Stage/skip entire files at once
- ✅ **No dependencies** - Pure Python standard library

## Quick Start

### Installation

=== "uv (recommended)"

    ```
    ❯ uv tool install git-stage-batch
    ```

=== "pipx"

    ```
    ❯ pipx install git-stage-batch
    ```

=== "pip"

    ```
    ❯ pip install git-stage-batch
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

```
# Start reviewing hunks
❯ git-stage-batch start

# Include the selected hunk (stage it)
❯ git-stage-batch include
# Or use the short alias:
❯ git-stage-batch i

# Skip it for now
❯ git-stage-batch skip    # or: s

# Discard it (remove from working tree)
❯ git-stage-batch discard # or: d

# For fine-grained control, stage specific lines
❯ git-stage-batch include --line 1,3,5-7  # or: il 1,3,5-7
❯ git-stage-batch skip --line 2,4         # or: sl 2,4

# Replacement text must use one contiguous displayed line-ID span
❯ git-stage-batch include --line 1-2 --as 'replacement'

# Exact unchanged edge anchors are stripped by default for line-scoped --as
❯ git-stage-batch include --line 1-2 --as 'keep1\nreplacement\nkeep4'

# Keep those anchors literally with --no-anchor
❯ git-stage-batch include --line 1-2 --as 'keep1\nreplacement\nkeep4' --no-anchor

# Or stage full replacement text for one file-scoped path
❯ git-stage-batch include --file path.txt --as 'full staged file text'

# Or replace one file-scoped working-tree path without staging it
❯ git-stage-batch discard --file path.txt --as 'full working tree text'

# Or preserve exact stdin text, including trailing newlines
❯ git-stage-batch include --file path.txt --as-stdin < replacement.txt
❯ git-stage-batch discard --file path.txt --as-stdin < replacement.txt

# Check status
❯ git-stage-batch status  # or: st

# Start fresh after committing
❯ git-stage-batch again   # or: a

# For advanced workflows, defer changes to named batches
❯ git-stage-batch include --to feature-work  # Save to batch for later
```

See [batch operations](batches.md) for advanced patch-series organization.
See [commands reference](commands.md) for the `--as` contiguous-range rules.

## Example Workflow

<div class="workflow-showcase">

```
# You have changes in multiple files
❯ git status
modified:   auth.py
modified:   config.py

# Start staging process
❯ git-stage-batch start
auth.py :: @@ -10,5 +10,5 @@
[#1] - old_hash_function()
[#2] + new_hash_function()
      validate_user()

# Include this for first commit
❯ git-stage-batch i
config.py :: @@ -20,3 +20,4 @@
[#1] + DEBUG = True
      TIMEOUT = 30

# This debug flag shouldn't be committed, skip it
❯ git-stage-batch s
No pending hunks.

# Create first commit
❯ git commit -m "auth: Upgrade to new hash function"

# Go through skipped hunks for next commit
❯ git-stage-batch a
config.py :: @@ -20,3 +20,4 @@
[#1] + DEBUG = True
      TIMEOUT = 30

# Discard this debug line instead
❯ git-stage-batch d
No pending hunks.

# Working tree is now clean
❯ git status
nothing to commit, working tree clean
```

</div>

## Important: Commit Early, Commit Often

`git-stage-batch` is designed for an incremental workflow:

1. Stage hunks that belong in the selected logical commit (`include`)
2. Create that commit (`git commit`)
3. Continue with remaining hunks (`again`)
4. Repeat

The `again` command shows you only the hunks you skipped - it doesn't re-show hunks you already included and committed.

<div class="section-separator"></div>

## Features

### Hunk-by-Hunk Staging

Review and stage individual hunks one at a time. Each hunk shows changed lines with IDs for easy reference.

### Line-by-Line Staging

Stage specific lines within a hunk:

```
❯ git-stage-batch include --line 1,3,5-7
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

<div class="section-separator"></div>

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

### When should I use batches?

Most workflows don't need batches. The core commands (`include`, `skip`, `discard`) handle typical staging scenarios.

Use batches when you need to:
- Defer specific changes for a separate commit while continuing to process other hunks
- Group related changes across multiple files for thematic organization
- Temporarily set aside changes you're uncertain about

See the [batch operations guide](batches.md) for detailed workflows and examples.

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
