"""Coverage for transformed appends after earlier same-file discards."""

from pathlib import Path
import subprocess

from .conftest import git_stage_batch


FIXTURE_ROOT = Path(__file__).parent / "fixtures"
GUEST_SMOKE_PATH = "scripts/vm/guest-smoke-test.sh"
GUEST_SMOKE_EXACT_LINE_IDS = "4-82,153-176,180-275"
GUEST_SMOKE_SHARED_LINE = (
    '"$runtime_dir/unplug-gate" "$runtime_dir/mode-gate"'
)
GUEST_SMOKE_REPLACEMENT = (
    '\t\t\t"$runtime_dir/mode-gate"\n'
    '\t\t\t"$runtime_dir/unplug-gate"\n'
)


def _commit_file(repo, content):
    file_path = repo / "file.txt"
    file_path.write_text(content)
    subprocess.run(
        ["git", "add", "file.txt"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add file"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    return file_path


def _show_file(repo, ref, path="file.txt"):
    return subprocess.run(
        ["git", "show", f"{ref}:{path}"],
        check=True,
        cwd=repo,
        capture_output=True,
        text=True,
    ).stdout


def _display_id_for_text(view, text):
    matches = [
        line for line in view.splitlines()
        if text in line and "[#" in line
    ]
    assert len(matches) == 1, matches
    return matches[0].split("[#", 1)[1].split("]", 1)[0]


def _display_id_range(view, first_text, last_text):
    first = int(_display_id_for_text(view, first_text))
    last = int(_display_id_for_text(view, last_text))
    assert first <= last
    return f"{first}-{last}"


def _prepare_guest_smoke_fixture(functional_repo):
    relative_path = GUEST_SMOKE_PATH
    file_path = functional_repo / relative_path
    file_path.parent.mkdir(parents=True)
    file_path.write_text(
        (FIXTURE_ROOT / "discard_transformed_append_baseline.sh").read_text()
    )
    subprocess.run(
        ["git", "add", relative_path],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Add guest smoke test"],
        check=True,
        cwd=functional_repo,
        capture_output=True,
    )
    file_path.write_text(
        (FIXTURE_ROOT / "discard_transformed_append_session.sh").read_text()
    )
    return relative_path, file_path


def _discard_guest_smoke_leading_batches(relative_path):
    git_stage_batch("start", "--no-auto-advance")
    first_view = git_stage_batch(
        "show", "--file", relative_path, "--page", "all"
    ).stdout
    assert "[#99]" in first_view
    assert "[#105]" in first_view
    git_stage_batch(
        "discard",
        "--to",
        "batch-a",
        "--line",
        "99-105",
        "--no-auto-advance",
    )

    second_view = git_stage_batch(
        "show", "--file", relative_path, "--page", "all"
    ).stdout
    assert "[#97]" in second_view
    assert "[#98]" in second_view
    assert "test ! -e ./castkms.ko" in second_view
    assert "test ! -e ./src/tests/castkms-kunit-tests.ko" in second_view
    git_stage_batch(
        "discard",
        "--to",
        "batch-b",
        "--line",
        "97-98",
        "--no-auto-advance",
    )


def _discard_guest_smoke_shared_line(relative_path):
    shared_view = git_stage_batch(
        "show", "--file", relative_path, "--page", "all"
    ).stdout
    shared_id = _display_id_for_text(
        shared_view,
        GUEST_SMOKE_SHARED_LINE,
    )
    result = git_stage_batch(
        "discard",
        "--to",
        "batch-c",
        "--line",
        shared_id,
        "--as-stdin",
        "--no-auto-advance",
        input_text=GUEST_SMOKE_REPLACEMENT,
        check=False,
    )
    assert result.returncode == 0, f"shared_id={shared_id}\n{result.stderr}"


def _batch_file_content(functional_repo, relative_path):
    return _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/batch-c",
        relative_path,
    )


def test_guest_smoke_transformed_append_before_exact_multirange(functional_repo):
    """A shared-line split should precede the remaining exact capture."""
    relative_path, file_path = _prepare_guest_smoke_fixture(functional_repo)

    _discard_guest_smoke_leading_batches(relative_path)
    _discard_guest_smoke_shared_line(relative_path)

    remaining_view = git_stage_batch(
        "show", "--file", relative_path, "--page", "all"
    ).stdout
    assert "[#4]" in remaining_view
    assert "mode_gate_open=0" in remaining_view
    assert "[#82]" in remaining_view
    assert "[#153]" in remaining_view
    assert "enable_writeback=0" in remaining_view
    assert "[#176]" in remaining_view
    assert "[#180]" in remaining_view
    assert "[#275]" in remaining_view
    exact_result = git_stage_batch(
        "discard",
        "--to",
        "batch-c",
        "--line",
        GUEST_SMOKE_EXACT_LINE_IDS,
        "--no-auto-advance",
        check=False,
    )
    assert exact_result.returncode == 0, (
        f"remaining_ids={GUEST_SMOKE_EXACT_LINE_IDS}\n"
        f"{exact_result.stderr}"
    )

    live_content = file_path.read_text()
    batch_content = _batch_file_content(functional_repo, relative_path)
    assert '\t\t\t"$runtime_dir/unplug-gate"\n' in live_content
    assert '\t\t\t"$runtime_dir/mode-gate"\n' not in live_content
    assert "append_crc_record()" not in live_content
    assert "run_writeback()" not in live_content
    assert "mode_gate_open=0" not in live_content
    assert "enable_writeback=1" not in live_content
    assert "enable_writeback=0" in live_content
    assert '\t\t\t"$runtime_dir/mode-gate"\n' in batch_content
    assert '\t\t\t"$runtime_dir/unplug-gate"\n' not in batch_content
    assert "append_crc_record()" in batch_content
    assert "run_writeback()" in batch_content
    assert "mode_gate_open=0" in batch_content
    assert "enable_writeback=1" in batch_content


def test_guest_smoke_transformed_append_after_exact_multirange(functional_repo):
    """A transformed shared line should append after exact multirange capture."""
    relative_path, file_path = _prepare_guest_smoke_fixture(functional_repo)
    _discard_guest_smoke_leading_batches(relative_path)

    git_stage_batch("show", "--file", relative_path, "--page", "all")
    git_stage_batch(
        "discard",
        "--to",
        "batch-c",
        "--line",
        GUEST_SMOKE_EXACT_LINE_IDS,
        "--no-auto-advance",
    )
    _discard_guest_smoke_shared_line(relative_path)
    live_content = file_path.read_text()
    batch_content = _batch_file_content(functional_repo, relative_path)
    assert '\t\t\t"$runtime_dir/unplug-gate"\n' in live_content
    assert '\t\t\t"$runtime_dir/mode-gate"\n' not in live_content
    assert (
        '\t\t\t"$runtime_dir/unplug-gate" "$runtime_dir/mode-gate"\n'
        not in live_content
    )
    assert '\t\t\t"$runtime_dir/mode-gate"\n' in batch_content
    assert '\t\t\t"$runtime_dir/unplug-gate"\n' not in batch_content


def test_transformed_append_after_prior_same_file_batches(functional_repo):
    """A transformed append should survive earlier same-file source advances."""
    file_path = _commit_file(
        functional_repo,
        "header\n"
        "scan-old-one\n"
        "scan-old-two\n"
        "clean-anchor\n"
        "runtime-anchor\n"
        "footer\n",
    )
    file_path.write_text(
        "header\n"
        "scan-new-one\n"
        "scan-new-two\n"
        "clean-one\n"
        "clean-two\n"
        "clean-anchor\n"
        "runtime-anchor\n"
        "mode-before-one\n"
        "mode-before-two\n"
        "unplug-one\n"
        "shared-unplug mode-gate\n"
        "unplug-two\n"
        "mode-after-one\n"
        "mode-after-two\n"
        "footer\n"
    )

    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    git_stage_batch(
        "discard",
        "--to",
        "batch-a",
        "--line",
        "1-4",
        "--no-auto-advance",
    )
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    git_stage_batch(
        "discard",
        "--to",
        "batch-b",
        "--line",
        "1-2",
        "--no-auto-advance",
    )
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    git_stage_batch(
        "discard",
        "--to",
        "batch-c",
        "--line",
        "1-2,6-7",
        "--no-auto-advance",
    )
    git_stage_batch("show", "--file", "file.txt", "--page", "all")
    result = git_stage_batch(
        "discard",
        "--to",
        "batch-c",
        "--line",
        "2",
        "--as-stdin",
        "--no-auto-advance",
        input_text="shared-unplug\n",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert file_path.read_text() == (
        "header\n"
        "scan-old-one\n"
        "scan-old-two\n"
        "clean-anchor\n"
        "runtime-anchor\n"
        "unplug-one\n"
        "unplug-two\n"
        "footer\n"
    )
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/batch-c",
    ) == (
        "header\n"
        "scan-old-one\n"
        "scan-old-two\n"
        "clean-anchor\n"
        "runtime-anchor\n"
        "mode-before-one\n"
        "mode-before-two\n"
        "shared-unplug\n"
        "mode-after-one\n"
        "mode-after-two\n"
        "footer\n"
    )


def test_transformed_selector_append_does_not_relocate_repeated_lines(functional_repo):
    """A selector split should not claim matching braces from its validator."""
    baseline = (
        "header\n"
        "plane-old\n"
        "middle\n"
        "int select_writer(int format)\n"
        "{\n"
        "\tswitch (format) {\n"
        "\tcase 1:\n"
        "\t\treturn old_writer;\n"
        "\tdefault:\n"
        "\t\tBUG();\n"
        "\t}\n"
        "}\n"
        "footer\n"
    )
    final = (
        "header\n"
        "plane-new\n"
        "int select_plane(int format)\n"
        "{\n"
        "\tfor (int i = 0; i < plane_count; i++)\n"
        "\t\tif (planes[i].format == format)\n"
        "\t\t\treturn planes[i].reader;\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT(select_plane);\n"
        "middle\n"
        "int select_writer(int format)\n"
        "{\n"
        "\tfor (int i = 0; i < writer_count; i++)\n"
        "\t\tif (writers[i].format == format)\n"
        "\t\t\treturn writers[i].writer;\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT(select_writer);\n"
        "bool registries_valid(void)\n"
        "{\n"
        "\tfor (int i = 0; i < plane_count; i++) {\n"
        "\t\tif (!planes[i].reader)\n"
        "\t\t\treturn false;\n"
        "\t}\n"
        "\n"
        "\tfor (int i = 0; i < writer_count; i++) {\n"
        "\t\tif (!writers[i].writer)\n"
        "\t\t\treturn false;\n"
        "\t}\n"
        "\n"
        "\treturn true;\n"
        "}\n"
        "EXPORT(registries_valid);\n"
        "footer\n"
    )
    live_selector = (
        "\tswitch (format) {\n"
        "\tcase 1:\n"
        "\t\treturn old_writer;\n"
        "\tdefault:\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "}\n"
        "EXPORT(select_writer);\n"
    )
    batch_selector = (
        "\tfor (int i = 0; i < writer_count; i++)\n"
        "\t\tif (writers[i].format == format)\n"
        "\t\t\treturn writers[i].writer;\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT(select_writer);\n"
    )
    expected_batch = (
        "header\n"
        "plane-new\n"
        "int select_plane(int format)\n"
        "{\n"
        "\tfor (int i = 0; i < plane_count; i++)\n"
        "\t\tif (planes[i].format == format)\n"
        "\t\t\treturn planes[i].reader;\n"
        "\treturn 0;\n"
        "}\n"
        "EXPORT(select_plane);\n"
        "middle\n"
        "int select_writer(int format)\n"
        "{\n"
        + batch_selector
        + "footer\n"
    )
    file_path = _commit_file(functional_repo, baseline)
    file_path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    context_ids = _display_id_range(view, "plane-old", "EXPORT(select_plane);")
    git_stage_batch(
        "include",
        "--to",
        "selector",
        "--line",
        context_ids,
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    selector_ids = _display_id_range(
        view,
        "\tswitch (format) {",
        "EXPORT(select_writer);",
    )
    result = git_stage_batch(
        "discard",
        "--to",
        "selector",
        "--line",
        selector_ids,
        "--as-stdin",
        "--no-auto-advance",
        input_text=batch_selector + live_selector,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    expected_live = final.replace(batch_selector, live_selector)
    actual = (
        file_path.read_text(),
        _show_file(
            functional_repo,
            "refs/git-stage-batch/batches/selector",
        ),
    )
    assert actual == (expected_live, expected_batch)
    live_content, batch_content = actual
    assert "BUG();" not in live_content
    assert live_content.count("EXPORT(select_writer);") == 1
    assert "\tswitch (format) {" not in batch_content
    assert "old_writer" not in batch_content
    assert "registries_valid" not in batch_content


def test_transformed_selector_stops_before_adjacent_validator(functional_repo):
    """A batch/live split should not claim matching validator structure."""
    old_doc = """ * castkms_get_pixel_write_function() - Retrieve the correct write_pixel function for a specific format.
 * The returned pointer is NULL for unsupported pixel formats. The caller must ensure that the
 * pointer is valid before using it in a castkms_writeback_job.
 *
 * @format: DRM_FORMAT_* value for which to obtain a conversion function (see [drm_fourcc.h])
 */
"""
    new_doc = """ * castkms_get_pixel_write_function() - Retrieve a format's write callback
 * @format: DRM_FORMAT_* value for which to obtain a conversion function
 *
 * Returns NULL when @format is unsupported.
 */
"""
    old_switch = """pixel_write_t castkms_get_pixel_write_function(u32 format)
{
	switch (format) {
	case DRM_FORMAT_ARGB8888:
		return &argb_u16_to_ARGB8888;
	case DRM_FORMAT_XRGB8888:
		return &argb_u16_to_XRGB8888;
	case DRM_FORMAT_ABGR8888:
		return &argb_u16_to_ABGR8888;
	case DRM_FORMAT_ARGB16161616:
		return &argb_u16_to_ARGB16161616;
	case DRM_FORMAT_XRGB16161616:
		return &argb_u16_to_XRGB16161616;
	case DRM_FORMAT_RGB565:
		return &argb_u16_to_RGB565;
	default:
		BUG();
	}
}
"""
    live_switch = old_switch.replace("\t\tBUG();\n", "\t\treturn NULL;\n")
    batch_selector = """pixel_write_t castkms_get_pixel_write_function(u32 format)
{
	for (unsigned int i = 0; i < ARRAY_SIZE(castkms_writeback_formats); i++)
		if (castkms_writeback_formats[i].format == format)
			return castkms_writeback_formats[i].write_pixel;

	return NULL;
}
"""
    selector_export = "EXPORT_SYMBOL_IF_KUNIT(castkms_get_pixel_write_function);\n"
    descriptors = """struct castkms_writeback_format {
	u32 format;
	pixel_write_t write_pixel;
};

static const struct castkms_writeback_format castkms_writeback_formats[] = {
	{ DRM_FORMAT_ARGB8888, argb_u16_to_ARGB8888 },
};

"""
    validator = """#if IS_ENABLED(CONFIG_KUNIT)
VISIBLE_IF_KUNIT bool castkms_format_registries_are_valid(void)
{
	for (unsigned int i = 0; i < ARRAY_SIZE(castkms_plane_formats); i++) {
		pixel_read_line_t read_line;
		u32 format = castkms_plane_formats[i].format;

		read_line = castkms_get_pixel_read_line_function(format);
		if (read_line != castkms_plane_formats[i].read_line)
			return false;
	}

	for (unsigned int i = 0; i < ARRAY_SIZE(castkms_writeback_formats); i++) {
		pixel_write_t write_pixel;
		u32 format = castkms_writeback_formats[i].format;

		write_pixel = castkms_get_pixel_write_function(format);
		if (write_pixel != castkms_writeback_formats[i].write_pixel)
			return false;
	}

	return true;
}
EXPORT_SYMBOL_IF_KUNIT(castkms_format_registries_are_valid);
#endif
"""
    prior = "".join(f"prior-change-{number:03}\n" for number in range(1, 370))
    baseline = "prefix\n/**\n" + old_doc + old_switch + "suffix\n"
    final = (
        "prefix\n" + prior + descriptors + "/**\n" + new_doc
        + batch_selector + selector_export + validator + "suffix\n"
    )
    path = _commit_file(functional_repo, baseline)
    path.write_text(final)

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    prior_first = int(_display_id_for_text(view, "prior-change-001"))
    descriptor_last = max(
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "};" in line and "[#" in line
    )
    git_stage_batch(
        "include", "--to", "descriptor-context", "--line",
        f"{prior_first}-{descriptor_last}", "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    first = int(_display_id_for_text(view, "Retrieve the correct write_pixel"))
    last = int(_display_id_for_text(view, "\treturn NULL;")) + 1
    payload = (
        FIXTURE_ROOT / "discard_transformed_selector_cross_context.txt"
    ).read_text()
    result = git_stage_batch(
        "discard", "--to", "selector", "--line", f"{first}-{last}",
        "--as-stdin", "--no-auto-advance", input_text=payload, check=False,
    )
    assert result.returncode == 0, result.stderr

    expected_live = (
        "prefix\n" + prior + descriptors + "/**\n" + old_doc
        + live_switch + selector_export + validator + "suffix\n"
    )
    expected_batch = "prefix\n/**\n" + new_doc + batch_selector + "suffix\n"
    assert path.read_text() == expected_live
    assert _show_file(functional_repo, "refs/git-stage-batch/batches/selector") == (
        expected_batch
    )


def test_new_batch_records_exact_addition_prefix_ownership(functional_repo):
    """A repeated suffix must not fragment a new batch's saved prefix."""
    prefix = (
        "#if FIRST\n"
        "first body\n"
        "shared line\n"
        "#endif /* FIRST end */\n"
    )
    suffix = (
        "#if SECOND\n"
        "second body\n"
        "shared line\n"
        "#endif /* SECOND end */\n"
    )
    path = _commit_file(functional_repo, "head\ntail\n")
    path.write_text("head\n" + prefix + "tail\n")

    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", "file.txt", "--page", "all").stdout
    selected = _display_id_range(view, "#if FIRST", "FIRST end")
    result = git_stage_batch(
        "discard", "--to", "exact-prefix", "--line", selected,
        "--as-stdin", "--no-auto-advance", input_text=prefix + suffix,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert path.read_text() == "head\n" + suffix + "tail\n"
    assert _show_file(
        functional_repo,
        "refs/git-stage-batch/batches/exact-prefix",
    ) == "head\n" + prefix + "tail\n"
