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
  subdir: 'git_stage_batch',
  install_tag: 'python-runtime',
)

# Build translations
subdir('po')
