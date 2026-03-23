project('git-stage-batch',
  version: files('VERSION'),
  license: 'MIT',
  meson_version: '>=1.2.0',
)

python = import('python').find_installation('python3', pure: false)
i18n = import('i18n')

# Generate POTFILES.in by scanning for translatable strings
run_command(
  python,
  meson.project_source_root() / 'scripts' / 'generate_potfiles.py',
  check: true,
)

subdir('src/git_stage_batch')
subdir('po')

configure_file(
  input: 'scripts/git-stage-batch.in',
  output: 'git-stage-batch',
  configuration: {'PYTHON': python.full_path()},
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
  install_tag: 'runtime',
)
