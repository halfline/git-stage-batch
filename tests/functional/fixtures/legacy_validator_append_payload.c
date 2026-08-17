#if IS_ENABLED(CONFIG_KUNIT)
VISIBLE_IF_KUNIT bool castkms_format_registries_are_valid(void)
{
	for (unsigned int i = 0; i < ARRAY_SIZE(castkms_plane_formats); i++) {
		pixel_read_line_t read_line;
		u32 format = castkms_plane_formats[i].format;

		read_line = castkms_get_pixel_read_line_function(format);
		if (read_line !=
				castkms_plane_formats[i].read_line)
			return false;
		for (unsigned int j = i + 1;
		     j < ARRAY_SIZE(castkms_plane_formats); j++)
			if (castkms_plane_formats[i].format ==
			    castkms_plane_formats[j].format)
				return false;
	}

	for (unsigned int i = 0; i < ARRAY_SIZE(castkms_writeback_formats); i++) {
		pixel_write_t write_pixel;
		u32 format = castkms_writeback_formats[i].format;

		write_pixel = castkms_get_pixel_write_function(format);
		if (write_pixel !=
				castkms_writeback_formats[i].write_pixel)
			return false;
		for (unsigned int j = i + 1;
		     j < ARRAY_SIZE(castkms_writeback_formats); j++)
			if (castkms_writeback_formats[i].format ==
			    castkms_writeback_formats[j].format)
				return false;
	}

	return true;
}
EXPORT_SYMBOL_IF_KUNIT(castkms_format_registries_are_valid);
#endif
#if IS_ENABLED(CONFIG_KUNIT)
VISIBLE_IF_KUNIT bool castkms_format_registries_are_valid(void)
{
	for (unsigned int i = 0; i < ARRAY_SIZE(castkms_plane_formats); i++) {
		pixel_read_line_t read_line;
		u32 format = castkms_plane_formats[i].format;

		read_line = castkms_get_pixel_read_line_function(format);
		if (read_line !=
				castkms_plane_formats[i].read_line)
			return false;
		for (unsigned int j = i + 1;
		     j < ARRAY_SIZE(castkms_plane_formats); j++)
			if (castkms_plane_formats[i].format ==
			    castkms_plane_formats[j].format)
				return false;
	}

	return true;
}
EXPORT_SYMBOL_IF_KUNIT(castkms_format_registries_are_valid);
#endif
