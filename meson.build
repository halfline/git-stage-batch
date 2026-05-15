project('git-stage-batch',
  version: files('VERSION'),
  license: 'MIT',
  meson_version: '>=1.10.0',
)

python = import('python').find_installation('python3', pure: true)
i18n = import('i18n')
package_dir = python.get_install_dir() / 'git_stage_batch'

# Generate POTFILES.in by scanning for translatable strings
run_command(
  python,
  meson.project_source_root() / 'scripts' / 'generate_potfiles.py',
  check: true,
)

subdir('src/git_stage_batch')
subdir('po')

install_subdir(
  'assets',
  install_dir: package_dir,
  install_tag: 'python-runtime',
)

configure_file(
  input: 'scripts/git-stage-batch.in',
  output: 'git-stage-batch',
  configuration: {'PYTHON': python.full_path()},
  install: true,
  install_dir: get_option('bindir'),
  install_mode: 'rwxr-xr-x',
  install_tag: 'runtime',
)

# Generate man pages for runtime help assets and system installation.
manpage_specs = [
  ['git-stage-batch.1.in', 'git-stage-batch.1'],
  ['git-stage-batch-abort.1.in', 'git-stage-batch-abort.1'],
  ['git-stage-batch-again.1.in', 'git-stage-batch-again.1'],
  ['git-stage-batch-annotate.1.in', 'git-stage-batch-annotate.1'],
  ['git-stage-batch-apply.1.in', 'git-stage-batch-apply.1'],
  ['git-stage-batch-block-file.1.in', 'git-stage-batch-block-file.1'],
  ['git-stage-batch-discard.1.in', 'git-stage-batch-discard.1'],
  ['git-stage-batch-drop.1.in', 'git-stage-batch-drop.1'],
  ['git-stage-batch-include.1.in', 'git-stage-batch-include.1'],
  ['git-stage-batch-install-assets.1.in', 'git-stage-batch-install-assets.1'],
  ['git-stage-batch-interactive.1.in', 'git-stage-batch-interactive.1'],
  ['git-stage-batch-list.1.in', 'git-stage-batch-list.1'],
  ['git-stage-batch-new.1.in', 'git-stage-batch-new.1'],
  ['git-stage-batch-redo.1.in', 'git-stage-batch-redo.1'],
  ['git-stage-batch-reset.1.in', 'git-stage-batch-reset.1'],
  ['git-stage-batch-show.1.in', 'git-stage-batch-show.1'],
  ['git-stage-batch-sift.1.in', 'git-stage-batch-sift.1'],
  ['git-stage-batch-skip.1.in', 'git-stage-batch-skip.1'],
  ['git-stage-batch-start.1.in', 'git-stage-batch-start.1'],
  ['git-stage-batch-status.1.in', 'git-stage-batch-status.1'],
  ['git-stage-batch-stop.1.in', 'git-stage-batch-stop.1'],
  ['git-stage-batch-suggest-fixup.1.in', 'git-stage-batch-suggest-fixup.1'],
  ['git-stage-batch-unblock-file.1.in', 'git-stage-batch-unblock-file.1'],
  ['git-stage-batch-undo.1.in', 'git-stage-batch-undo.1'],
]
manpages = []
foreach manpage_spec : manpage_specs
  manpages += configure_file(
    input: 'man' / manpage_spec[0],
    output: manpage_spec[1],
    configuration: {'VERSION': meson.project_version()},
  )
endforeach
install_data(
  manpages,
  install_dir: package_dir / 'assets' / 'man' / 'man1',
  install_tag: 'python-runtime',
)
if not get_option('python_wheel')
  install_man(manpages)
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
