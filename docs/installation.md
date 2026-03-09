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

- **Python 3.13+**
- No other dependencies (pure stdlib!)

## Verify Installation

```
❯ git-stage-batch --version
```

You should see output showing the installed version.

## Next Steps

- [Quick Start Guide](index.md#quick-start)
- [Interactive Mode](interactive.md)
- [Commands Reference](commands.md)
