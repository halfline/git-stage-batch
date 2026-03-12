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
