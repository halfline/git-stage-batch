"""Regression coverage for replacement replay before a duplicate suffix."""

import subprocess

from .conftest import git_stage_batch


def _display_ids_for_text(view: str, text: str) -> list[int]:
    return [
        int(line.split("[#", 1)[1].split("]", 1)[0])
        for line in view.splitlines()
        if "[#" in line and text in line
    ]


def _index_text(repo, path) -> str:
    return subprocess.run(
        ["git", "show", f":{path.name}"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit_file(repo, path, content: str, message: str) -> None:
    path.write_text(content)
    subprocess.run(
        ["git", "add", path.name],
        cwd=repo,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def test_replacement_include_stops_before_duplicate_future_suffix(functional_repo):
    """A source replacement must not absorb an unselected repeated program."""
    path = functional_repo / "cec.c"
    header = "// SPDX-License-Identifier: GPL-2.0-only\n\n"
    query = (
        "static int query_caps(void)\n"
        "{\n"
        "    return CAP_EDID;\n"
        "}\n"
    )
    bind = (
        "\nstatic int bind_transport(void)\n"
        "{\n"
        "    if (!transport_id)\n"
        "        return -EINVAL;\n"
        "    return bind_owner();\n"
        "}\n"
    )
    online = (
        "\nstatic int set_transport_online(void)\n"
        "{\n"
        "    return publish_ready();\n"
        "}\n"
    )
    later = (
        "\nstatic int transmit(void)\n"
        "{\n"
        "    return queue_message();\n"
        "}\n"
    )
    predecessor = header + query + bind
    target = header + query + bind + online
    repaired_predecessor = predecessor.replace(
        "        return -EINVAL;\n",
        "        release_transport();\n        return -EINVAL;\n",
    )
    repaired_target = target.replace(
        "        return -EINVAL;\n",
        "        release_transport();\n        return -EINVAL;\n",
    )
    future_suffix = predecessor + later

    # The replacement occupies the first complete program. A later concern is
    # another complete program beginning with the same source, so the far edge
    # must be chosen from ownership rather than repeated textual context.
    path.write_text(target + future_suffix)
    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch(
        "show", "--file", path.name, "--page", "all"
    ).stdout
    spdx = _display_ids_for_text(view, "SPDX-License-Identifier")
    assert len(spdx) == 2, spdx
    peeled = git_stage_batch(
        "discard",
        "--to",
        "transport-online",
        "--line",
        f"{spdx[0]}-{spdx[1] - 1}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=predecessor,
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == predecessor + future_suffix
    git_stage_batch("stop")

    _commit_file(functional_repo, path, predecessor, "Add CEC query")
    _commit_file(functional_repo, path, repaired_predecessor, "Repair cleanup")

    (functional_repo / "future.txt").write_text("later concern\n")
    git_stage_batch("start", "--no-auto-advance")
    replay = git_stage_batch(
        "include",
        "--from",
        "transport-online",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert _index_text(functional_repo, path) == repaired_target
    assert path.read_text() == repaired_target


def test_replacement_apply_consumes_identical_source_tail(functional_repo):
    """Applying a wrapper must consume its identical predecessor tail."""
    path = functional_repo / "Kbuild"
    prefix = "obj-m += castkms.o\n\ncastkms-y := src/castkms_drv.o\n\n"
    cec_object = "castkms-y += src/castkms_cec_core.o src/castkms_cec_uapi.o\n"
    cec_flag = "ccflags-y += -DCASTKMS_HAVE_CEC=1\n"
    suffix = "\nccflags-y += -I$(src)/src\n"
    wrapper = (
        "ifeq ($(CASTKMS_BUILD_CEC),y)\n"
        "ifneq ($(filter y m,$(CONFIG_DRM_DISPLAY_HDMI_CEC_HELPER)),)\n"
        + cec_object
        + cec_flag
        + "endif\n"
        "endif\n"
    )
    baseline = prefix + suffix
    batch_source = prefix + wrapper + cec_object + suffix
    predecessor = prefix + cec_object + cec_flag + suffix
    target = prefix + wrapper + suffix

    _commit_file(functional_repo, path, baseline, "Add base objects")
    path.write_text(batch_source)
    git_stage_batch("start", "--no-auto-advance")
    view = git_stage_batch("show", "--file", path.name, "--page", "all").stdout
    first = _display_ids_for_text(view, "ifeq ($(CASTKMS_BUILD_CEC),y)")[0]
    last = _display_ids_for_text(view, "endif")[-1]
    peeled = git_stage_batch(
        "discard",
        "--to",
        "cec-build-selection",
        "--line",
        f"{first}-{last}",
        "--as-stdin",
        "--no-edge-overlap",
        "--no-auto-advance",
        input_text=cec_object,
        check=False,
    )
    assert peeled.returncode == 0, peeled.stderr
    assert path.read_text() == prefix + cec_object + cec_object + suffix
    git_stage_batch("stop")

    _commit_file(functional_repo, path, predecessor, "Add CEC objects")

    (functional_repo / "future.txt").write_text("later concern\n")
    git_stage_batch("start", "--no-auto-advance")
    replay = git_stage_batch(
        "apply",
        "--from",
        "cec-build-selection",
        "--file",
        path.name,
        check=False,
    )
    assert replay.returncode == 0, replay.stderr
    assert _index_text(functional_repo, path) == predecessor
    assert path.read_text() == target
