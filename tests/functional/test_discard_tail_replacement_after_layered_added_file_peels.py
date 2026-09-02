"""Regression coverage for a tail replacement after layered added-file peels."""

from .conftest import git_stage_batch


def _display_id_for_text(view, text):
    matches = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(matches) == 1, matches
    return int(matches[0].split("[#", 1)[1].split("]", 1)[0])


def test_tail_replacement_survives_prior_region_and_file_peels(functional_repo):
    """Replacing the last function must preserve the unselected added-file prefix."""
    path = functional_repo / "source.c"
    header = functional_repo / "bridge.h"
    target_tail = (
        "static int build_output(const struct options *options, char **data, int *size)\n"
        "{\n"
        "\tchar *generated;\n"
        "\tint result;\n"
        "\n"
        "\tif (options->path)\n"
        "\t\treturn read_file(options->path, data, size);\n"
        "\n"
        "\tgenerated = allocate(BLOCK_SIZE);\n"
        "\tif (!generated)\n"
        "\t\treturn -1;\n"
        "\tresult = fill_named(generated, options->name);\n"
        "\tif (result < 0) {\n"
        '\t\treport(options->name ? "invalid name" : "default failed");\n'
        "\t\tfree(generated);\n"
        "\t\treturn result;\n"
        "\t}\n"
        "\n"
        "\t*data = generated;\n"
        "\t*size = BLOCK_SIZE;\n"
        "\treturn 0;\n"
        "}\n"
    )
    predecessor_tail = (
        "static int build_output(const struct options *options, char **data, int *size)\n"
        "{\n"
        "\tchar *generated;\n"
        "\tint result;\n"
        "\n"
        "\tif (options->path)\n"
        "\t\treturn read_file(options->path, data, size);\n"
        "\tif (!options->name) {\n"
        "\t\t*data = NULL;\n"
        "\t\t*size = 0;\n"
        "\t\treturn 0;\n"
        "\t}\n"
        "\n"
        "\tgenerated = allocate(BLOCK_SIZE);\n"
        "\tif (!generated)\n"
        "\t\treturn -1;\n"
        "\tresult = fill_named(generated, options->name);\n"
        "\tif (result < 0) {\n"
        '\t\treport("invalid name");\n'
        "\t\tfree(generated);\n"
        "\t\treturn result;\n"
        "\t}\n"
        "\n"
        "\t*data = generated;\n"
        "\t*size = BLOCK_SIZE;\n"
        "\treturn 0;\n"
        "}\n"
    )
    target = (
        "// license\n"
        "\n"
        '#include "bridge.h"\n'
        "\n"
        "struct options {\n"
        "\tconst char *path;\n"
        "\tconst char *name;\n"
        "};\n"
        "\n"
        "void fail(struct bridge *bridge)\n"
        "{\n"
        "\tbridge->failed = true;\n"
        "\tif (bridge->loop)\n"
        "\t\tquit_loop(bridge->loop);\n"
        "}\n"
        "\n"
        "static int parse_id(const char *value)\n"
        "{\n"
        "\treturn value ? 0 : -1;\n"
        "}\n"
        "\n"
        "static int read_file(const char *path, char **data, int *size)\n"
        "{\n"
        "\treturn path && data && size ? 0 : -1;\n"
        "}\n"
        "\n" + target_tail
    )
    after_stream = target.replace(
        "\tif (bridge->loop)\n\t\tquit_loop(bridge->loop);\n",
        "",
    )
    after_bridge = after_stream.replace('#include "bridge.h"\n\n', "").replace(
        "void fail(struct bridge *bridge)\n{\n\tbridge->failed = true;\n}\n\n",
        "",
    )
    predecessor = after_bridge.replace(target_tail, predecessor_tail)

    path.write_text(target)
    header.write_text("struct bridge { int failed; void *loop; };\n")
    git_stage_batch("start", "--no-auto-advance")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    loop = _display_id_for_text(view, "if (bridge->loop)")
    quit_line = _display_id_for_text(view, "quit_loop")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "stream-node",
        "--line",
        f"{loop}-{quit_line}",
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == after_stream

    git_stage_batch("show", "--file", header.name, "--page", "all")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "bridge-state",
        "--file",
        header.name,
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    include = _display_id_for_text(view, '#include "bridge.h"')
    fail = _display_id_for_text(view, "void fail")
    parse = _display_id_for_text(view, "static int parse_id")
    peeled = git_stage_batch(
        "discard",
        "--to",
        "bridge-state",
        "--line",
        f"{include}-{include + 1},{fail}-{parse - 1}",
        "--no-auto-advance",
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == after_bridge

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    first = _display_id_for_text(view, "static int build_output")
    tail = max(
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line
    )
    replacement = git_stage_batch(
        "discard",
        "--to",
        "default-output",
        "--line",
        f"{first}-{tail}",
        "--as-stdin",
        "--no-auto-advance",
        input_text=predecessor_tail,
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr
    assert path.read_text() == predecessor


def test_blank_line_replacement_preserves_first_inserted_line(functional_repo):
    """A replacement must not drop the first line inserted at an empty gap."""
    path = functional_repo / "source.c"
    path.write_text("head\n\ntail\n")
    git_stage_batch("start", "--no-auto-advance")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    head = _display_id_for_text(view, "head")
    replacement = git_stage_batch(
        "discard",
        "--to",
        "default-output",
        "--line",
        str(head + 1),
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=("if (!name) {\n\t*data = NULL;\n\treturn 0;\n}\n\n"),
        check=False,
    )
    assert replacement.returncode == 0, replacement.stderr
    assert path.read_text() == (
        "head\nif (!name) {\n\t*data = NULL;\n\treturn 0;\n}\n\ntail\n"
    )
