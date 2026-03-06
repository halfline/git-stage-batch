if get_option('python_wheel')
  gettext_result = i18n.gettext(
    'git-stage-batch',
    install : false,
  )
  mo_targets = gettext_result[0]

  cp_prog = find_program('cp')

  foreach mo : mo_targets
    # Extract language from path (e.g., '.../po/es/LC_MESSAGES/git-stage-batch.mo' -> 'es')
    mo_path = mo.full_path()
    path_parts = mo_path.split('/')
    lang = path_parts[path_parts.length() - 3]

    custom_target(
      'wheel-mo-' + lang,
      input : mo,
      output : 'git-stage-batch.mo',
      command : [cp_prog, '@INPUT@', '@OUTPUT@'],
      build_by_default : true,
      build_subdir : lang,
      install : true,
      install_dir : py.get_install_dir(
        subdir : 'git_stage_batch/locale/' + lang + '/LC_MESSAGES'
      ),
    )
  endforeach
else
  i18n.gettext('git-stage-batch')
endif
