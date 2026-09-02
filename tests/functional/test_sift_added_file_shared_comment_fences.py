"""Regression coverage for sifting an added-file replacement around fences."""

from .conftest import git_stage_batch


def _save_replacement(path, batch, predecessor):
    saved = git_stage_batch(
        "include",
        "--to",
        batch,
        "--file",
        path.name,
        "--no-auto-advance",
        check=False,
    )
    assert saved.returncode == 0, saved.stderr
    path.write_text(predecessor)
    narrowed = git_stage_batch(
        "sift", "--from", batch, "--to", batch, check=False
    )
    return narrowed


def test_sift_added_file_replacement_around_shared_comment_fences(functional_repo):
    """Shared comment fences must not make a narrow replacement look foreign."""
    path = functional_repo / "protocol.h"
    target = (
        "#define DRM_CASTKMS_CAPTURE_START_EXCLUSIVE\t(1U << 0)\n"
        "\n"
        "/**\n"
        " * DRM_CASTKMS_CAPTURE_START_EXCLUDE_CURSOR:\n"
        " *\n"
        " * Exclude the cursor plane from captured frame composition. With\n"
        " * DRM_CASTKMS_GRANT_READ_CURSOR, position and image metadata are still\n"
        " * reported in capture events so consumers can render the cursor client-side.\n"
        " * Without that right all cursor fields and bitmaps are suppressed.\n"
        " */\n"
        "#define DRM_CASTKMS_CAPTURE_START_EXCLUDE_CURSOR (1U << 1)\n"
        "\n"
        "/**\n"
        " * struct drm_castkms_capture_start - start an exclusive capture stream\n"
        " * @crtc_id: DRM object ID of the CRTC to observe\n"
        " * @flags: DRM_CASTKMS_CAPTURE_START_EXCLUSIVE, optionally combined with\n"
        " *         DRM_CASTKMS_CAPTURE_START_EXCLUDE_CURSOR\n"
        " * @stream_id: file-local stream identifier returned by the driver\n"
        " * @reserved: must be zero\n"
        " * @mode_generation: current CRTC mode generation returned by the driver\n"
        " *\n"
        " * Starting capture does not activate or otherwise change the selected CRTC.\n"
        " * Another DRM file cannot start a stream for that CRTC until the owner stops\n"
        " * its stream or closes the file.\n"
        " */\n"
        "struct drm_castkms_capture_start {\n"
        "\t__u32 crtc_id;\n"
        "\t__u32 flags;\n"
        "\t__u32 stream_id;\n"
        "\t__u32 reserved;\n"
        "\t__u64 mode_generation;\n"
        "};\n"
        "\n"
        "/**\n"
    )
    predecessor = (
        "#define DRM_CASTKMS_CAPTURE_START_EXCLUSIVE\t(1U << 0)\n"
        "\n"
        "/**\n"
        " *\n"
        " */\n"
        "\n"
        "/**\n"
        " * struct drm_castkms_capture_start - start an exclusive capture stream\n"
        " * @crtc_id: DRM object ID of the CRTC to observe\n"
        " * @flags: must be DRM_CASTKMS_CAPTURE_START_EXCLUSIVE\n"
        " * @stream_id: file-local stream identifier returned by the driver\n"
        " * @reserved: must be zero\n"
        " * @mode_generation: current CRTC mode generation returned by the driver\n"
        " *\n"
        " * Starting capture does not activate or otherwise change the selected CRTC.\n"
        " * Another DRM file cannot start a stream for that CRTC until the owner stops\n"
        " * its stream or closes the file.\n"
        " */\n"
        "struct drm_castkms_capture_start {\n"
        "\t__u32 crtc_id;\n"
        "\t__u32 flags;\n"
        "\t__u32 stream_id;\n"
        "\t__u32 reserved;\n"
        "\t__u64 mode_generation;\n"
        "};\n"
        "\n"
        "/**\n"
    )
    path.write_text(target)

    git_stage_batch("start", "--no-auto-advance")
    sifted = _save_replacement(path, "cursor-exclusion", predecessor)
    assert sifted.returncode == 0, sifted.stderr
    assert path.read_text() == predecessor
