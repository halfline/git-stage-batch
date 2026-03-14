project('git-stage-batch',
  version: files('VERSION'),
  license: 'MIT',
  meson_version: '>=1.2.0',
)

python = import('python').find_installation('python3', pure: false)
i18n = import('i18n')

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

manpage = configure_file(
  input: 'docs/git-stage-batch.1.in',
  output: 'git-stage-batch.1',
  configuration: {'VERSION': meson.project_version()},
)

if not get_option('python_wheel')
  install_man(manpage)
endif

# Install bash completion (skip for wheels, include for system install)
if not get_option('python_wheel')
  install_data(
    'completions/git-stage-batch',
    install_dir: get_option('datadir') / 'bash-completion' / 'completions',
    install_tag: 'runtime',
  )
  install_data(
    'completions/git-stage-batch-prompt.sh',
    install_dir: get_option('datadir') / 'git-stage-batch',
    install_tag: 'runtime',
  )
endif
