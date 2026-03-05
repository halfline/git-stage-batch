# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.2.0] - 2025-03-04

### Added

- **Interactive mode** (`--interactive` flag) - Beginner-friendly workflow similar to `git add -p` with single-letter shortcuts
- **Colored diff output** - ANSI color support with automatic TTY detection
  - Green for additions, red for deletions, cyan for headers
  - Muted gray for line numbers to improve scannability
  - Automatically disabled when piped or redirected
- **Stale state detection** - Automatically detects and clears cached state when files are committed or modified externally
- **Compact range formatting** - Status command displays remaining line IDs in compact notation (e.g., `1-10` instead of `1,2,3,4,5,6,7,8,9,10`)
- **Machine-readable output** - `--porcelain` flag for `status` and `show` commands for scripting
- **File-level operations** - `include-file` and `skip-file` commands to process all hunks in current file
- **Permanent file exclusion** - `block-file` and `unblock-file` commands to manage .gitignore entries
- **Short command aliases** - All commands now have short aliases (e.g., `i` for `include`, `st` for `status`)

### Changed

- Renamed "exclude" terminology to "skip" throughout codebase for clarity
- `start` command now resets state when a session is already in progress (instead of erroring)
- Bare command (no arguments) defaults to `include` when session is active

### Fixed

- Status command no longer shows misleading stale state after external commits
- Improved error messages throughout

## [0.1.0] - Initial Release

### Added

- Core hunk-by-hunk staging functionality
- Line-level staging with `include-line`, `skip-line`, `discard-line`
- State persistence across command invocations
- Command-based workflow optimized for automation and AI assistants
