project('git-stage-batch',
  version: files('VERSION'),
  license: 'MIT',
  meson_version: '>=1.2.0',
)

py = import('python').find_installation('python3', pure: false)
i18n = import('i18n')

# Generate version file from template
version_py = configure_file(
  input: 'src/git_stage_batch/_version.py.in',
  output: '_version.py',
  configuration: {'VERSION': meson.project_version()},
  install: true,
  install_dir: py.get_install_dir() / 'git_stage_batch',
  install_tag: 'python-runtime',
)

# Install Python package (tagged for wheel inclusion)
py.install_sources(
  'src/git_stage_batch/__init__.py',
  'src/git_stage_batch/i18n.py',
  'src/git_stage_batch/cli.py',
  'src/git_stage_batch/commands.py',
  'src/git_stage_batch/display.py',
  'src/git_stage_batch/editor.py',
  'src/git_stage_batch/hashing.py',
  'src/git_stage_batch/line_selection.py',
  'src/git_stage_batch/models.py',
  'src/git_stage_batch/parser.py',
  'src/git_stage_batch/state.py',
  subdir: 'git_stage_batch',
  install_tag: 'python-runtime',
)

# Build translations
subdir('po')

# Install entry point script
configure_file(
  input: 'scripts/git-stage-batch.in',
  output: 'git-stage-batch',
  configuration: {'PYTHON': py.full_path()},
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
  install_tag: 'runtime',
)

# Generate and install man page (skip for wheels, include for system install)
manpage = configure_file(
  input: 'docs/git-stage-batch.1.in',
  output: 'git-stage-batch.1',
  configuration: {'VERSION': meson.project_version()},
)
if not get_option('python_wheel')
  install_man(manpage)
endif
