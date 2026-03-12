if get_option('python_wheel')
  gettext_result = i18n.gettext(
    'git-stage-batch',
    install : false,
  )
  message_objects = gettext_result[0]

  cp = find_program('cp')

  foreach message_object : message_objects
    # Extract language from path (e.g., '.../po/es/LC_MESSAGES/git-stage-batch.mo' -> 'es')
    message_object_path = message_object.full_path()
    path_parts = message_object_path.split('/')
    lang = path_parts[path_parts.length() - 3]

    custom_target(
      'wheel-mo-' + lang,
      input : message_object,
      output : 'git-stage-batch.mo',
      command : [cp, '@INPUT@', '@OUTPUT@'],
      build_by_default : true,
      build_subdir : lang,
      install : true,
      install_dir : python.get_install_dir(
        subdir : 'git_stage_batch/locale/' + lang + '/LC_MESSAGES'
      ),
    )
  endforeach
else
  i18n.gettext('git-stage-batch')
endif
