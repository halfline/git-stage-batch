# Recording the Demo GIF

To create the demo GIF animation for the documentation:

## Option 1: Using VHS (Recommended)

[VHS](https://github.com/charmbracelet/vhs) generates beautiful terminal GIFs from script files.

### Install VHS

```bash
# macOS
brew install vhs

# Linux (download binary)
wget https://github.com/charmbracelet/vhs/releases/latest/download/vhs_Linux_x86_64.tar.gz
tar -xzf vhs_Linux_x86_64.tar.gz
sudo mv vhs /usr/local/bin/

# Or with Go
go install github.com/charmbracelet/vhs@latest
```

### Generate the GIF

```bash
# From the repository root
vhs demo.tape

# This creates demo.gif
```

### Add to Documentation

Move the generated GIF:

```bash
cp demo.gif docs/assets/demo.gif
```

## Option 2: Using asciinema

```bash
# Install
pip install asciinema agg

# Record
asciinema rec demo.cast
# (perform demo manually)
# Press Ctrl+D when done

# Convert to GIF
agg demo.cast demo.gif

# Move to docs
cp demo.gif docs/assets/demo.gif
```

## Update Documentation

Once you have `demo.gif`, embed it in `docs/index.md`:

```markdown
## See it in Action

![git-stage-batch demo](assets/demo.gif)
```
