# Installation

## Recommended (uv)

Install once, use everywhere:

```
❯ uv tool install git-stage-batch
```

This installs `git-stage-batch` as a global command-line tool.

<div class="section-separator"></div>

## Alternative Methods

### pipx

```
❯ pipx install git-stage-batch
```

### pip

```
❯ pip install git-stage-batch
```

### meson (system install)

For system package managers or building from source:

```
# Clone the repository
❯ git clone https://github.com/halfline/git-stage-batch.git
❯ cd git-stage-batch

# Configure and build
❯ meson setup build
❯ meson compile -C build

# Install to system (requires root)
❯ sudo meson install -C build
```

Or install to a custom prefix:

```
❯ meson setup build --prefix=/usr/local
❯ meson compile -C build
❯ meson install -C build
```

This installs:
- Python modules to `lib/python*/site-packages/`
- Translations to `share/locale/`
- Executable to `bin/`
- Man page to `share/man/man1/`
- Documentation to `share/doc/git-stage-batch/`

### Try Without Installing

```
❯ uvx git-stage-batch start
```

This runs the tool without permanently installing it.

<div class="section-separator"></div>

## Requirements

- **Python 3.10 through 3.13**
- **Git 2.29 or newer** available on `PATH`
- **POSIX operating system.** Linux is tested in CI; native Windows is not
  supported because repository locking, signals, symlinks, and terminal process
  control currently use POSIX facilities.

There are no runtime Python package dependencies. Building from source also
requires Meson, meson-python, Ninja, and gettext. Repository paths are handled
with Git's byte-preserving surrogate-escape conventions; symlink workflows need
a filesystem and account that permit creating symlinks.

## Verify Installation

```
❯ git-stage-batch --version
```

You should see output showing the installed version.

## Next Steps

- [Quick Start Guide](index.md#quick-start)
- [Commands Reference](commands.md)
