configure_file(
  input: '_version.py.in',
  output: '_version.py',
  configuration: {'VERSION': meson.project_version()},
  install: true,
  install_dir: package_dir,
  install_tag: 'python-runtime',
)

install_subdir(
  '.',
  install_dir: package_dir,
  exclude_files: [
    '_version.py.in',
    'meson.build',
  ],
  install_tag: 'python-runtime',
)
