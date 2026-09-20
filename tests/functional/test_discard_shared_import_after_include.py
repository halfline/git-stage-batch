"""Exercise a replacement within a copied import wrapper in an added file."""

import pytest

from .conftest import git_stage_batch


def _line_id(view: str, text: str) -> str:
    lines = [line for line in view.splitlines() if text in line and "[#" in line]
    assert len(lines) == 1, lines
    return lines[0].split("[#", 1)[1].split("]", 1)[0]


@pytest.mark.parametrize("no_edge_overlap", [False, True])
def test_discard_shared_import_entry_keeps_reduced_import(
    functional_repo, no_edge_overlap
):
    path = functional_repo / "main.rs"
    final = (
        "use pronk_renderer_service::{\n"
        "    PrivatePoolConfig, RendererStream, RendererStreamConfig, RendererStreamState,\n"
        "};\n"
        "\n"
        "fn main() {}\n"
    )
    reduced = final.replace(
        "RendererStreamConfig, RendererStreamState,",
        "RendererStreamConfig,",
    )
    path.write_text(final)
    git_stage_batch("start", "--no-auto-advance")
    git_stage_batch("new", "multi-output", "--note", "Reconcile active outputs")

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    wrapper = ",".join(
        _line_id(view, text)
        for text in ("use pronk_renderer_service::{", "};")
    )
    git_stage_batch(
        "include", "--to", "multi-output", "--line", wrapper,
        "--no-auto-advance",
    )

    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    entry = _line_id(view, "PrivatePoolConfig, RendererStream")
    args = [
        "discard", "--to", "multi-output", "--line", entry,
        "--as-stdin", "--no-auto-advance",
    ]
    if no_edge_overlap:
        args.append("--no-edge-overlap")
    result = git_stage_batch(
        *args,
        input_text="    PrivatePoolConfig, RendererStream, RendererStreamConfig,\n",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert path.read_text() == reduced

    result = git_stage_batch("apply", "--from", "multi-output", check=False)
    assert result.returncode == 0, result.stderr
    assert path.read_text() == final
