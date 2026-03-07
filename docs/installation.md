# Installation

## Recommended (uv)

Install once, use everywhere:

```bash
uv tool install git-stage-batch
```

This installs `git-stage-batch` as a global command-line tool.

## Alternative Methods

### pipx

```bash
pipx install git-stage-batch
```

### pip

```bash
pip install git-stage-batch
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

### Try Without Installing

```bash
uvx git-stage-batch start
```

This runs the tool without permanently installing it.

## Requirements

- **Python 3.13+**
- No other dependencies (pure stdlib!)

## Verify Installation

```bash
git-stage-batch --version
```

You should see:
```
git-stage-batch 0.2.0
```

## Next Steps

- [Quick Start Guide](index.md#quick-start)
- [Interactive Mode](interactive.md)
- [Commands Reference](commands.md)
