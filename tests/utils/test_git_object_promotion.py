"""Tests for bounded Git object promotion."""

import gc
import os
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

from git_stage_batch.utils import git_object_promotion
from git_stage_batch.utils.git_command import run_git_command
from git_stage_batch.utils.git_object_io import (
    create_git_blob,
    get_git_object_type,
    temporary_git_object_environment,
)
from git_stage_batch.utils.git_object_promotion import (
    GitObjectPromotionLease,
    promote_git_object_closure,
    release_git_object_promotion_lease,
)


@pytest.fixture
def temp_git_repo(tmp_path, monkeypatch):
    """Create a temporary Git repository for promotion tests."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init"], check=True, cwd=repo, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        cwd=repo,
        capture_output=True,
    )

    (repo / "README.md").write_text("# Test\n")
    subprocess.run(
        ["git", "add", "README.md"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        cwd=repo,
        capture_output=True,
    )
    return repo


def _quarantined_candidate_commit(quarantine, chunks):
    environment = quarantine.environment()
    base_commit = run_git_command(
        ["rev-parse", "HEAD"],
        requires_index_lock=False,
    ).stdout.strip()
    blob_id = create_git_blob(chunks, env=environment)
    tree_id = run_git_command(
        ["mktree"],
        stdin_chunks=[f"100644 blob {blob_id}\tcandidate.bin\n".encode("ascii")],
        env=environment,
        requires_index_lock=False,
    ).stdout.strip()
    commit_id = run_git_command(
        ["commit-tree", tree_id, "-p", base_commit],
        stdin_chunks=[b"Candidate object closure\n"],
        env=environment,
        requires_index_lock=False,
    ).stdout.strip()
    return base_commit, blob_id, tree_id, commit_id


def _promotion_artifact_path(lease, suffix):
    return Path(
        run_git_command(
            [
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                f"objects/pack/pack-{lease.pack_hash}.{suffix}",
            ],
            requires_index_lock=False,
        ).stdout.strip()
    )


def test_promote_git_object_closure_publishes_only_after_strict_indexing(
    temp_git_repo,
):
    """A quarantined commit closure should become durable as one checked pack."""
    subprocess.run(
        ["git", "config", "pack.writeReverseIndex", "true"],
        check=True,
        capture_output=True,
    )
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"candidate payload\n"],
        )
        assert get_git_object_type(commit_id) is None
        assert get_git_object_type(tree_id) is None
        assert get_git_object_type(blob_id) is None

        lease = promote_git_object_closure(
            quarantine,
            lease_id="publish-success",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )

        assert isinstance(lease, GitObjectPromotionLease)
        assert len(lease.pack_hash) == 40
        assert lease.object_format == "sha1"
        assert lease.keep_device >= 0
        assert lease.keep_inode > 0
        assert lease.keep_changed_ns >= 0
        assert _promotion_artifact_path(lease, "keep").read_bytes() == (
            b"publish-success\n"
        )
        assert not _promotion_artifact_path(lease, "rev").exists()
        assert get_git_object_type(commit_id) == "commit"
        assert get_git_object_type(tree_id) == "tree"
        assert get_git_object_type(blob_id) == "blob"
        assert (
            run_git_command(
                ["cat-file", "blob", blob_id],
                text_output=False,
                requires_index_lock=False,
            ).stdout
            == b"candidate payload\n"
        )


def test_promote_git_object_closure_retries_only_its_exact_lease(temp_git_repo):
    """A same-lease retry is idempotent and a foreign lease cannot take it."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"retry payload\n"],
        )
        arguments = {
            "include": (commit_id,),
            "exclude": (base_commit,),
            "expected_objects": {
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        }
        first = promote_git_object_closure(
            quarantine,
            lease_id="retry-same-lease",
            **arguments,
        )
        retried = promote_git_object_closure(
            quarantine,
            lease_id="retry-same-lease",
            **arguments,
        )

        assert retried == first
        keep_path = _promotion_artifact_path(first, "keep")
        assert keep_path.read_bytes() == b"retry-same-lease\n"

        with pytest.raises(RuntimeError, match="keep file does not match"):
            promote_git_object_closure(
                quarantine,
                lease_id="foreign-lease",
                **arguments,
            )
        assert keep_path.read_bytes() == b"retry-same-lease\n"


def test_promote_git_object_closure_adopts_exact_lease_after_config_change(
    temp_git_repo,
    monkeypatch,
):
    """A retry must authenticate its durable pack instead of regenerating it."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"config-independent retry\n"],
        )
        arguments = {
            "include": (commit_id,),
            "exclude": (base_commit,),
            "expected_objects": {
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        }
        first = promote_git_object_closure(
            quarantine,
            lease_id="config-independent-retry",
            **arguments,
        )
        run_git_command(
            ["config", "pack.compression", "9"],
            requires_index_lock=False,
        )
        run_git_command(
            ["config", "pack.threads", "4"],
            requires_index_lock=False,
        )

        def unexpected_pipeline(*args, **kwargs):
            pytest.fail("an exact durable lease must bypass pack generation")

        monkeypatch.setattr(
            git_object_promotion,
            "_git_pack_pipeline",
            unexpected_pipeline,
        )
        retried = promote_git_object_closure(
            quarantine,
            lease_id="config-independent-retry",
            **arguments,
        )

    assert retried == first


def test_promote_git_object_closure_rejects_incomplete_exact_lease(
    temp_git_repo,
    monkeypatch,
):
    """An exact keep without its authenticated pack must block regeneration."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"incomplete exact lease\n"],
        )
        pack_directory = Path(
            run_git_command(
                [
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-path",
                    "objects/pack",
                ],
                requires_index_lock=False,
            ).stdout.strip()
        )
        keep_path = pack_directory / f"pack-{'0' * 40}.keep"
        keep_path.write_bytes(b"incomplete-exact-lease\n")
        keep_path.chmod(0o600)

        def unexpected_pipeline(*args, **kwargs):
            pytest.fail("an incomplete exact lease must block pack generation")

        monkeypatch.setattr(
            git_object_promotion,
            "_git_pack_pipeline",
            unexpected_pipeline,
        )
        monkeypatch.setattr(
            git_object_promotion,
            "resolve_git_objects",
            unexpected_pipeline,
        )
        with pytest.raises(RuntimeError, match="Cannot inspect Git pack artifact"):
            promote_git_object_closure(
                quarantine,
                lease_id="incomplete-exact-lease",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )

        assert get_git_object_type(commit_id) is None


def test_promote_git_object_closure_rejects_malformed_exact_lease(
    temp_git_repo,
    monkeypatch,
):
    """An exact keep with non-private metadata must not trigger regeneration."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"malformed exact lease\n"],
        )
        arguments = {
            "include": (commit_id,),
            "exclude": (base_commit,),
            "expected_objects": {
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        }
        lease = promote_git_object_closure(
            quarantine,
            lease_id="malformed-exact-lease",
            **arguments,
        )
        _promotion_artifact_path(lease, "keep").chmod(0o644)

        def unexpected_pipeline(*args, **kwargs):
            pytest.fail("a malformed exact lease must block pack generation")

        monkeypatch.setattr(
            git_object_promotion,
            "_git_pack_pipeline",
            unexpected_pipeline,
        )
        monkeypatch.setattr(
            git_object_promotion,
            "resolve_git_objects",
            unexpected_pipeline,
        )
        with pytest.raises(RuntimeError, match="artifact identity changed"):
            promote_git_object_closure(
                quarantine,
                lease_id="malformed-exact-lease",
                **arguments,
            )


def test_promote_git_object_closure_rejects_nonregular_native_keep(
    temp_git_repo,
    monkeypatch,
):
    """A native keep symlink cannot be classified as an unrelated lease."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"nonregular retry candidate\n"],
        )
        pack_directory = Path(
            run_git_command(
                [
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-path",
                    "objects/pack",
                ],
                requires_index_lock=False,
            ).stdout.strip()
        )
        foreign_keep = pack_directory / "foreign.keep"
        foreign_keep.write_bytes(b"nonregular-retry-candidate\n")
        native_keep = pack_directory / f"pack-{'0' * 40}.keep"
        native_keep.symlink_to(foreign_keep.name)

        def unexpected_pipeline(*args, **kwargs):
            pytest.fail("a nonregular native keep must block pack generation")

        monkeypatch.setattr(
            git_object_promotion,
            "_git_pack_pipeline",
            unexpected_pipeline,
        )
        monkeypatch.setattr(
            git_object_promotion,
            "resolve_git_objects",
            unexpected_pipeline,
        )
        with pytest.raises(RuntimeError, match="not a regular file"):
            promote_git_object_closure(
                quarantine,
                lease_id="nonregular-retry-candidate",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )

        assert get_git_object_type(commit_id) is None


def test_promote_git_object_closure_rejects_ambiguous_exact_leases(
    temp_git_repo,
    monkeypatch,
):
    """Two exact keep candidates must fail before either can be adopted."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"ambiguous exact leases\n"],
        )
        pack_directory = Path(
            run_git_command(
                [
                    "rev-parse",
                    "--path-format=absolute",
                    "--git-path",
                    "objects/pack",
                ],
                requires_index_lock=False,
            ).stdout.strip()
        )
        for pack_hash in ("0" * 40, "1" * 40):
            keep_path = pack_directory / f"pack-{pack_hash}.keep"
            keep_path.write_bytes(b"ambiguous-exact-leases\n")
            keep_path.chmod(0o600)

        def unexpected_pipeline(*args, **kwargs):
            pytest.fail("ambiguous exact leases must block pack generation")

        monkeypatch.setattr(
            git_object_promotion,
            "_git_pack_pipeline",
            unexpected_pipeline,
        )
        monkeypatch.setattr(
            git_object_promotion,
            "resolve_git_objects",
            unexpected_pipeline,
        )
        with pytest.raises(RuntimeError, match="Multiple Git pack leases"):
            promote_git_object_closure(
                quarantine,
                lease_id="ambiguous-exact-leases",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )

        assert get_git_object_type(commit_id) is None


def test_promote_git_object_closure_rejects_partial_clone_before_object_checks(
    temp_git_repo,
    monkeypatch,
):
    """Partial-clone config must fail before resolving or publishing objects."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"partial clone rejection\n"],
        )
        run_git_command(
            ["config", "core.repositoryFormatVersion", "1"],
            requires_index_lock=False,
        )
        run_git_command(
            ["config", "extensions.partialClone", "origin"],
            requires_index_lock=False,
        )

        def unexpected_work(*args, **kwargs):
            pytest.fail("partial repositories must fail before object work")

        monkeypatch.setattr(
            git_object_promotion,
            "resolve_git_objects",
            unexpected_work,
        )
        monkeypatch.setattr(
            git_object_promotion,
            "_git_pack_pipeline",
            unexpected_work,
        )
        with pytest.raises(RuntimeError, match="partial clone"):
            promote_git_object_closure(
                quarantine,
                lease_id="reject-partial-clone",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )

    assert get_git_object_type(commit_id) is None
    assert get_git_object_type(tree_id) is None
    assert get_git_object_type(blob_id) is None


def test_promote_git_object_closure_rejects_remote_partial_clone_filter(
    temp_git_repo,
    monkeypatch,
):
    """A filter-only remote config must fail before resolving candidate objects."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"partial clone filter rejection\n"],
        )
        run_git_command(
            ["config", "remote.OrIgIn.PartialCloneFilter", "blob:none"],
            requires_index_lock=False,
        )

        def unexpected_work(*args, **kwargs):
            pytest.fail("partial-clone filters must fail before object work")

        monkeypatch.setattr(
            git_object_promotion,
            "resolve_git_objects",
            unexpected_work,
        )
        monkeypatch.setattr(
            git_object_promotion,
            "_git_pack_pipeline",
            unexpected_work,
        )
        with pytest.raises(RuntimeError, match="partial clone filter"):
            promote_git_object_closure(
                quarantine,
                lease_id="reject-partial-clone-filter",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )

    assert get_git_object_type(commit_id) is None
    assert get_git_object_type(tree_id) is None
    assert get_git_object_type(blob_id) is None


@pytest.mark.parametrize("promisor_value", ("yes", "invalid-boolean"))
def test_promote_git_object_closure_rejects_promisor_config_operationally(
    temp_git_repo,
    monkeypatch,
    promisor_value,
):
    """Git must parse promisor booleans, and malformed config must fail closed."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [f"promisor config {promisor_value}\n".encode("ascii")],
        )
        run_git_command(
            ["config", "remote.origin.promisor", promisor_value],
            requires_index_lock=False,
        )

        def unexpected_work(*args, **kwargs):
            pytest.fail("promisor repositories must fail before object work")

        monkeypatch.setattr(
            git_object_promotion,
            "resolve_git_objects",
            unexpected_work,
        )
        monkeypatch.setattr(
            git_object_promotion,
            "_git_pack_pipeline",
            unexpected_work,
        )
        message = (
            "promisor repositories"
            if promisor_value == "yes"
            else "Cannot inspect Git promisor configuration"
        )
        with pytest.raises(RuntimeError, match=message):
            promote_git_object_closure(
                quarantine,
                lease_id="reject-promisor-config",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )

    assert get_git_object_type(commit_id) is None
    assert get_git_object_type(tree_id) is None
    assert get_git_object_type(blob_id) is None


def test_promote_git_object_closure_rejects_promisor_pack_sidecar(
    temp_git_repo,
    monkeypatch,
):
    """A pinned promisor-pack marker must reject promotion before resolution."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"promisor pack rejection\n"],
        )
        pack_directory = Path(
            run_git_command(
                ["rev-parse", "--path-format=absolute", "--git-path", "objects/pack"],
                requires_index_lock=False,
            ).stdout.strip()
        )
        (pack_directory / "pack-hostile.promisor").write_bytes(b"")

        def unexpected_work(*args, **kwargs):
            pytest.fail("promisor packs must fail before object work")

        monkeypatch.setattr(
            git_object_promotion,
            "resolve_git_objects",
            unexpected_work,
        )
        monkeypatch.setattr(
            git_object_promotion,
            "_git_pack_pipeline",
            unexpected_work,
        )
        with pytest.raises(RuntimeError, match="promisor repositories"):
            promote_git_object_closure(
                quarantine,
                lease_id="reject-promisor-pack",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )

    assert get_git_object_type(commit_id) is None
    assert get_git_object_type(tree_id) is None
    assert get_git_object_type(blob_id) is None


def test_promote_git_object_closure_allows_explicit_non_promisor_remote(
    temp_git_repo,
):
    """An operational false promisor boolean must not reject a complete repo."""
    run_git_command(
        ["config", "remote.origin.promisor", "no"],
        requires_index_lock=False,
    )
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"complete repository\n"],
        )

        lease = promote_git_object_closure(
            quarantine,
            lease_id="allow-complete-repository",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )

    assert _promotion_artifact_path(lease, "keep").is_file()
    assert get_git_object_type(commit_id) == "commit"


@pytest.mark.parametrize(
    "response",
    (
        b"keep " + b"0" * 40 + b"\n",
        b"keep\t" + b"0" * 40,
        b"keep\t" + b"0" * 40 + b"\nextra\n",
        b"other\t" + b"0" * 40 + b"\n",
        b"keep\t" + b"A" * 40 + b"\n",
        b"keep\tshort\n",
        b"keep\t" + b"0" * 40 + b"\xff\n",
    ),
)
def test_git_object_promotion_rejects_malformed_index_pack_response(response):
    """The lease marker protocol must accept one exact native response line."""
    with pytest.raises(RuntimeError, match="unexpected response"):
        git_object_promotion._lease_from_index_pack_output(
            response,
            lease_id="parser-test",
            object_format="sha1",
        )


@pytest.mark.parametrize("marker", (b"keep", b"pack"))
def test_git_object_promotion_accepts_exact_first_and_retry_responses(marker):
    """Git's first-install and existing-pack responses must parse exactly."""
    pack_hash = "1" * 40

    lease = git_object_promotion._lease_from_index_pack_output(
        marker + b"\t" + pack_hash.encode("ascii") + b"\n",
        lease_id="parser-first-or-retry",
        object_format="sha1",
    )

    assert lease == GitObjectPromotionLease(
        lease_id="parser-first-or-retry",
        pack_hash=pack_hash,
        object_format="sha1",
        keep_device=-1,
        keep_inode=-1,
        keep_changed_ns=-1,
        prior_released_device=None,
        prior_released_inode=None,
    )


def test_promoted_pack_lease_preserves_unreachable_objects_through_prune(
    temp_git_repo,
):
    """The keep must protect candidate objects until a ref can anchor them."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"prune-protected payload\n"],
        )
        lease = promote_git_object_closure(
            quarantine,
            lease_id="prune-protection",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )

    subprocess.run(
        ["git", "gc", "--prune=now"],
        check=True,
        capture_output=True,
    )

    assert _promotion_artifact_path(lease, "keep").is_file()
    assert get_git_object_type(commit_id) == "commit"
    assert get_git_object_type(tree_id) == "tree"
    assert get_git_object_type(blob_id) == "blob"


def test_release_git_object_promotion_lease_is_durable_and_idempotent(
    temp_git_repo,
):
    """Post-ref release should durably remove its keep and recovery marker."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"release payload\n"],
        )
        lease = promote_git_object_closure(
            quarantine,
            lease_id="release-after-ref",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )

        assert release_git_object_promotion_lease(quarantine, lease) is True
        assert release_git_object_promotion_lease(quarantine, lease) is False

    keep_path = _promotion_artifact_path(lease, "keep")
    released = list(
        keep_path.parent.glob(f".git-stage-batch-released-{lease.pack_hash}-*")
    )
    assert not keep_path.exists()
    assert released == []


def test_release_git_object_promotion_lease_recovers_after_cleanup_fsync_failure(
    temp_git_repo,
    monkeypatch,
):
    """A crash after marker unlink must resume as an already-clean release."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"release cleanup fsync failure\n"],
        )
        lease = promote_git_object_closure(
            quarantine,
            lease_id="release-cleanup-fsync",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )
        real_fsync_directories = git_object_promotion._fsync_release_directories
        fsync_count = 0

        def injected_fsync_directories(object_directory, pack_directory):
            nonlocal fsync_count
            fsync_count += 1
            if fsync_count == 2:
                raise OSError("injected cleanup directory fsync failure")
            real_fsync_directories(object_directory, pack_directory)

        monkeypatch.setattr(
            git_object_promotion,
            "_fsync_release_directories",
            injected_fsync_directories,
        )
        with pytest.raises(
            OSError,
            match="injected cleanup directory fsync failure",
        ):
            release_git_object_promotion_lease(quarantine, lease)
        monkeypatch.setattr(
            git_object_promotion,
            "_fsync_release_directories",
            real_fsync_directories,
        )

        assert release_git_object_promotion_lease(quarantine, lease) is False

    keep_path = _promotion_artifact_path(lease, "keep")
    assert fsync_count == 2
    assert not keep_path.exists()
    assert not list(
        keep_path.parent.glob(f".git-stage-batch-released-{lease.pack_hash}-*")
    )


def test_release_git_object_promotion_lease_recovers_after_unlink_failure(
    temp_git_repo,
    monkeypatch,
):
    """A durable marker left by failed unlink must be cleaned on retry."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"release cleanup unlink failure\n"],
        )
        lease = promote_git_object_closure(
            quarantine,
            lease_id="release-cleanup-unlink",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )
        real_unlink = git_object_promotion.os.unlink
        injected = False

        def injected_unlink(path, *args, **kwargs):
            nonlocal injected
            if not injected and str(path).startswith(".git-stage-batch-released-"):
                injected = True
                raise OSError("injected marker unlink failure")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(git_object_promotion.os, "unlink", injected_unlink)
        with pytest.raises(OSError, match="injected marker unlink failure"):
            release_git_object_promotion_lease(quarantine, lease)
        monkeypatch.setattr(git_object_promotion.os, "unlink", real_unlink)

        assert release_git_object_promotion_lease(quarantine, lease) is False

    keep_path = _promotion_artifact_path(lease, "keep")
    assert injected is True
    assert not keep_path.exists()
    assert not list(
        keep_path.parent.glob(f".git-stage-batch-released-{lease.pack_hash}-*")
    )


def test_release_git_object_promotion_lease_handles_reacquired_keep(
    temp_git_repo,
    monkeypatch,
):
    """A resumed promotion may recover and clean a prior release marker."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"reacquired release payload\n"],
        )
        arguments = {
            "include": (commit_id,),
            "exclude": (base_commit,),
            "expected_objects": {
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        }
        first = promote_git_object_closure(
            quarantine,
            lease_id="release-after-resume",
            **arguments,
        )
        real_unlink = git_object_promotion.os.unlink
        injected = False

        def injected_unlink(path, *args, **kwargs):
            nonlocal injected
            if not injected and str(path).startswith(".git-stage-batch-released-"):
                injected = True
                raise OSError("injected release cleanup failure")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(git_object_promotion.os, "unlink", injected_unlink)
        with pytest.raises(OSError, match="injected release cleanup failure"):
            release_git_object_promotion_lease(quarantine, first)
        monkeypatch.setattr(git_object_promotion.os, "unlink", real_unlink)
        assert injected is True

        retried = promote_git_object_closure(
            quarantine,
            lease_id="release-after-resume",
            **arguments,
        )
        assert retried.lease_id == first.lease_id
        assert retried.pack_hash == first.pack_hash
        assert retried.object_format == first.object_format
        assert (retried.keep_device, retried.keep_inode) != (
            first.keep_device,
            first.keep_inode,
        )
        assert (
            retried.prior_released_device,
            retried.prior_released_inode,
        ) == (first.keep_device, first.keep_inode)
        assert _promotion_artifact_path(retried, "keep").is_file()
        keep_path = _promotion_artifact_path(retried, "keep")
        canonical_marker = next(
            keep_path.parent.glob(f".git-stage-batch-released-{retried.pack_hash}-*")
        )
        displaced_marker = canonical_marker.with_suffix(".displaced")
        canonical_marker.rename(displaced_marker)
        canonical_marker.write_bytes(b"release-after-resume\n")
        canonical_marker.chmod(0o600)
        with pytest.raises(RuntimeError, match="lease identity"):
            release_git_object_promotion_lease(quarantine, retried)
        canonical_marker.unlink()
        displaced_marker.rename(canonical_marker)
        assert release_git_object_promotion_lease(quarantine, retried) is True
        assert release_git_object_promotion_lease(quarantine, retried) is False

    keep_path = _promotion_artifact_path(first, "keep")
    released = list(
        keep_path.parent.glob(f".git-stage-batch-released-{first.pack_hash}-*")
    )
    assert not keep_path.exists()
    assert released == []


def test_release_git_object_promotion_lease_bounds_repeated_recovery_markers(
    temp_git_repo,
    monkeypatch,
):
    """Repeated reacquisition must reuse and ultimately remove one marker name."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"repeated release recovery\n"],
        )
        arguments = {
            "include": (commit_id,),
            "exclude": (base_commit,),
            "expected_objects": {
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        }
        first = promote_git_object_closure(
            quarantine,
            lease_id="repeated-release-recovery",
            **arguments,
        )
        real_unlink = git_object_promotion.os.unlink
        failed_first_release = False

        def fail_first_marker_unlink(path, *args, **kwargs):
            nonlocal failed_first_release
            if not failed_first_release and str(path).startswith(
                ".git-stage-batch-released-"
            ):
                failed_first_release = True
                raise OSError("injected first-generation marker failure")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(
            git_object_promotion.os,
            "unlink",
            fail_first_marker_unlink,
        )
        with pytest.raises(OSError, match="first-generation marker failure"):
            release_git_object_promotion_lease(quarantine, first)
        monkeypatch.setattr(git_object_promotion.os, "unlink", real_unlink)

        second = promote_git_object_closure(
            quarantine,
            lease_id="repeated-release-recovery",
            **arguments,
        )
        failed_second_release = False

        def fail_second_generation_marker(path, *args, **kwargs):
            nonlocal failed_second_release
            if str(path).startswith(".git-stage-batch-released-"):
                metadata = os.stat(
                    path,
                    dir_fd=kwargs.get("dir_fd"),
                    follow_symlinks=False,
                )
                if metadata.st_ino == second.keep_inode and not failed_second_release:
                    failed_second_release = True
                    raise OSError("injected second-generation marker failure")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(
            git_object_promotion.os,
            "unlink",
            fail_second_generation_marker,
        )
        with pytest.raises(OSError, match="second-generation marker failure"):
            release_git_object_promotion_lease(quarantine, second)
        monkeypatch.setattr(git_object_promotion.os, "unlink", real_unlink)

        third = promote_git_object_closure(
            quarantine,
            lease_id="repeated-release-recovery",
            **arguments,
        )
        assert release_git_object_promotion_lease(quarantine, third) is True
        assert release_git_object_promotion_lease(quarantine, third) is False

    keep_path = _promotion_artifact_path(third, "keep")
    assert failed_first_release is True
    assert failed_second_release is True
    assert not keep_path.exists()
    assert not list(
        keep_path.parent.glob(f".git-stage-batch-released-{third.pack_hash}-*")
    )


def test_release_git_object_promotion_lease_restores_raced_foreign_node(
    temp_git_repo,
    monkeypatch,
):
    """A keep-name swap at rename must be restored without deleting the node."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"release rename race payload\n"],
        )
        lease = promote_git_object_closure(
            quarantine,
            lease_id="release-rename-race",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )
        keep_path = _promotion_artifact_path(lease, "keep")
        displaced_keep = keep_path.with_suffix(".attacker-displaced")
        real_rename_noreplace = git_object_promotion._rename_noreplace
        injected = False

        def injected_rename_noreplace(parent, source_name, destination_name):
            nonlocal injected
            if not injected and source_name == keep_path.name:
                injected = True
                keep_path.rename(displaced_keep)
                keep_path.write_bytes(b"foreign raced node\n")
            real_rename_noreplace(parent, source_name, destination_name)

        monkeypatch.setattr(
            git_object_promotion,
            "_rename_noreplace",
            injected_rename_noreplace,
        )
        with pytest.raises(RuntimeError, match="keep file identity changed"):
            release_git_object_promotion_lease(quarantine, lease)

    assert injected is True
    assert keep_path.read_bytes() == b"foreign raced node\n"
    assert displaced_keep.read_bytes() == b"release-rename-race\n"
    assert not list(
        keep_path.parent.glob(f".git-stage-batch-released-{lease.pack_hash}-*")
    )


def test_release_git_object_promotion_lease_rejects_tampered_recovery_marker(
    temp_git_repo,
    monkeypatch,
):
    """Idempotent recovery must authenticate its durable released marker."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"release recovery marker payload\n"],
        )
        lease = promote_git_object_closure(
            quarantine,
            lease_id="release-marker-tamper",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )
        real_unlink = git_object_promotion.os.unlink

        def injected_unlink(path, *args, **kwargs):
            if str(path).startswith(".git-stage-batch-released-"):
                raise OSError("injected release cleanup failure")
            real_unlink(path, *args, **kwargs)

        monkeypatch.setattr(git_object_promotion.os, "unlink", injected_unlink)
        with pytest.raises(OSError, match="injected release cleanup failure"):
            release_git_object_promotion_lease(quarantine, lease)
        monkeypatch.setattr(git_object_promotion.os, "unlink", real_unlink)
        keep_path = _promotion_artifact_path(lease, "keep")
        released = list(
            keep_path.parent.glob(f".git-stage-batch-released-{lease.pack_hash}-*")
        )
        assert len(released) == 1
        released_identity = released[0].stat()
        replacement = released[0].with_name(f"{released[0].name}-replacement")
        replacement.write_bytes(b"release-marker-tamper\n")
        replacement.chmod(0o600)
        replacement_identity = replacement.stat()
        assert (
            replacement_identity.st_dev,
            replacement_identity.st_ino,
        ) != (released_identity.st_dev, released_identity.st_ino)
        replacement.replace(released[0])

        with pytest.raises(RuntimeError, match="identity"):
            release_git_object_promotion_lease(quarantine, lease)


@pytest.mark.parametrize(
    "tamper",
    (
        "keep-content",
        "keep-mode",
        "keep-replacement",
        "keep-symlink",
        "keep-hardlink",
        "keep-fifo",
        "pack-content",
        "index-symlink",
    ),
)
def test_release_git_object_promotion_lease_rejects_tampered_artifacts(
    temp_git_repo,
    tamper,
):
    """Release must leave a malformed or substituted lease untouched."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [f"tamper {tamper}\n".encode("ascii")],
        )
        lease = promote_git_object_closure(
            quarantine,
            lease_id=f"tamper-{tamper}",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )
        keep_path = _promotion_artifact_path(lease, "keep")
        pack_path = _promotion_artifact_path(lease, "pack")
        index_path = _promotion_artifact_path(lease, "idx")

        if tamper == "keep-content":
            keep_path.write_bytes(b"foreign\n")
        elif tamper == "keep-mode":
            keep_path.chmod(0o644)
        elif tamper == "keep-replacement":
            keep_path.unlink()
            keep_path.write_bytes(f"tamper-{tamper}\n".encode("ascii"))
            keep_path.chmod(0o600)
        elif tamper == "keep-symlink":
            keep_path.unlink()
            keep_path.symlink_to(pack_path.name)
        elif tamper == "keep-hardlink":
            os.link(keep_path, keep_path.with_suffix(".alias"))
        elif tamper == "keep-fifo":
            keep_path.unlink()
            os.mkfifo(keep_path)
        elif tamper == "pack-content":
            pack_path.chmod(0o644)
            with pack_path.open("r+b") as pack_file:
                pack_file.write(b"FAIL")
            pack_path.chmod(0o444)
        else:
            assert tamper == "index-symlink"
            index_path.unlink()
            index_path.symlink_to(pack_path.name)

        with pytest.raises(RuntimeError, match="Git pack"):
            release_git_object_promotion_lease(quarantine, lease)
        assert os.path.lexists(keep_path)


def test_promote_git_object_closure_recovers_after_fsync_failure(
    temp_git_repo,
    monkeypatch,
):
    """A durable same-lease retry should adopt a keep left by failed fsync."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"fsync retry payload\n"],
        )
        real_fsync = git_object_promotion.os.fsync
        fsync_count = 0

        def injected_fsync(file_descriptor):
            nonlocal fsync_count
            fsync_count += 1
            if fsync_count == 3:
                raise OSError("injected keep fsync failure")
            real_fsync(file_descriptor)

        monkeypatch.setattr(git_object_promotion.os, "fsync", injected_fsync)
        with pytest.raises(OSError, match="injected keep fsync failure"):
            promote_git_object_closure(
                quarantine,
                lease_id="fsync-retry",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )
        monkeypatch.setattr(git_object_promotion.os, "fsync", real_fsync)

        lease = promote_git_object_closure(
            quarantine,
            lease_id="fsync-retry",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )

    assert fsync_count == 3
    assert _promotion_artifact_path(lease, "keep").read_bytes() == b"fsync-retry\n"


def test_pack_directory_inventory_uses_independent_stream_position(tmp_path):
    """Pack inventories must start independently of the pinned stream."""
    pack_directory = tmp_path / "pack"
    pack_directory.mkdir()
    marker_name = "pack-marker.keep"
    (pack_directory / marker_name).touch()
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(pack_directory, flags)
    try:
        try:
            original_position = os.lseek(descriptor, 0, os.SEEK_CUR)
            pinned_position = os.lseek(descriptor, 17, os.SEEK_SET)
        except OSError:
            pytest.skip("Directory stream positioning is unavailable")
        try:
            with git_object_promotion._fresh_pack_directory_entries(
                descriptor
            ) as entries:
                assert tuple(entry.name for entry in entries) == (marker_name,)
            assert os.lseek(descriptor, 0, os.SEEK_CUR) == pinned_position
        finally:
            os.lseek(descriptor, original_position, os.SEEK_SET)
    finally:
        os.close(descriptor)


def test_promote_git_object_closure_rejects_pack_directory_symlink(
    temp_git_repo,
):
    """A substituted pack directory must fail before candidate publication."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"pack directory substitution\n"],
        )
        pack_directory = Path(
            run_git_command(
                ["rev-parse", "--path-format=absolute", "--git-path", "objects/pack"],
                requires_index_lock=False,
            ).stdout.strip()
        )
        displaced = pack_directory.with_name("pack-displaced")
        pack_directory.rename(displaced)
        pack_directory.symlink_to(displaced.name, target_is_directory=True)

        with pytest.raises(RuntimeError, match="pack path is not a directory"):
            promote_git_object_closure(
                quarantine,
                lease_id="pack-directory-substitution",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )

        assert get_git_object_type(commit_id) is None
        assert get_git_object_type(tree_id) is None
        assert get_git_object_type(blob_id) is None


def test_promote_git_object_closure_rejects_object_store_identity_change(
    temp_git_repo,
):
    """A changed persistent object-directory inode must invalidate quarantine."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"object identity substitution\n"],
        )
        object_directory = Path(
            run_git_command(
                ["rev-parse", "--path-format=absolute", "--git-path", "objects"],
                requires_index_lock=False,
            ).stdout.strip()
        )
        displaced = object_directory.with_name("objects-displaced")
        object_directory.rename(displaced)
        object_directory.mkdir()

        with pytest.raises(RuntimeError, match="object store identity changed"):
            promote_git_object_closure(
                quarantine,
                lease_id="object-directory-substitution",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )


def test_promotion_installs_through_pinned_object_directory_during_aba_swap(
    temp_git_repo,
    monkeypatch,
):
    """The index consumer must not follow a transient visible object-dir swap."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"pinned object directory\n"],
        )
        object_directory = Path(
            run_git_command(
                ["rev-parse", "--path-format=absolute", "--git-path", "objects"],
                requires_index_lock=False,
            ).stdout.strip()
        )
        displaced = object_directory.with_name("objects-pinned")
        raced = object_directory.with_name("objects-raced")
        real_start_command = git_object_promotion.start_command
        consumer_started = False
        producer_started = False

        def swapping_start_command(arguments, **kwargs):
            nonlocal consumer_started, producer_started
            assert kwargs["env"]["GIT_ALLOW_PROTOCOL"] == ""
            assert kwargs["env"]["GIT_NO_LAZY_FETCH"] == "1"
            assert kwargs["env"]["GIT_NO_REPLACE_OBJECTS"] == "1"
            if "pack-objects" in arguments:
                producer_started = True
                assert "--compression=0" in arguments
                assert "--threads=1" in arguments
                return real_start_command(arguments, **kwargs)
            if "index-pack" not in arguments:
                return real_start_command(arguments, **kwargs)
            consumer_started = True
            assert arguments[:6] == [
                "git",
                "-c",
                "core.fsync=pack,pack-metadata",
                "-c",
                "core.fsyncMethod=fsync",
                "index-pack",
            ]
            assert "--keep=pinned-object-directory" in arguments
            assert "--no-rev-index" in arguments
            consumer_environment = kwargs["env"]
            if sys.platform == "darwin":
                assert "GIT_OBJECT_DIRECTORY" not in consumer_environment
                descriptor = int(
                    consumer_environment[
                        git_object_promotion.DARWIN_OBJECT_DIRECTORY_DESCRIPTOR
                    ]
                )
                assert descriptor in kwargs["pass_fds"]
            else:
                assert consumer_environment["GIT_OBJECT_DIRECTORY"].startswith(
                    "/proc/self/fd/"
                )
            object_directory.rename(displaced)
            object_directory.mkdir()
            (object_directory / "pack").mkdir()
            try:
                return real_start_command(arguments, **kwargs)
            finally:
                object_directory.rename(raced)
                displaced.rename(object_directory)

        monkeypatch.setattr(
            git_object_promotion,
            "start_command",
            swapping_start_command,
        )
        lease = promote_git_object_closure(
            quarantine,
            lease_id="pinned-object-directory",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )

    assert consumer_started is True
    assert producer_started is True
    assert _promotion_artifact_path(lease, "keep").is_file()
    assert list((raced / "pack").iterdir()) == []
    assert get_git_object_type(commit_id) == "commit"


def test_promote_git_object_closure_supports_sha256_repositories(
    tmp_path,
    monkeypatch,
):
    """Promotion should use the repository's native object-ID width."""
    repository = tmp_path / "sha256"
    repository.mkdir()
    monkeypatch.chdir(repository)
    initialized = subprocess.run(
        ["git", "init", "--object-format=sha256"],
        check=False,
        capture_output=True,
    )
    if initialized.returncode != 0:
        pytest.skip("installed Git does not support SHA-256 repositories")
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True,
        capture_output=True,
    )
    (repository / "README.md").write_text("# SHA-256\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        check=True,
        capture_output=True,
    )

    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"sha256 candidate\n"],
        )
        lease = promote_git_object_closure(
            quarantine,
            lease_id="sha256-promotion",
            include=(commit_id,),
            exclude=(base_commit,),
            expected_objects={
                commit_id: "commit",
                tree_id: "tree",
                blob_id: "blob",
            },
        )
        assert release_git_object_promotion_lease(quarantine, lease) is True
        assert release_git_object_promotion_lease(quarantine, lease) is False

    assert len(lease.pack_hash) == 64
    assert lease.object_format == "sha256"
    assert get_git_object_type(commit_id) == "commit"
    assert get_git_object_type(tree_id) == "tree"
    assert get_git_object_type(blob_id) == "blob"


def test_promote_git_object_closure_rejects_untrusted_inputs(temp_git_repo):
    """Promotion arguments must be certified native-width object identities."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"untrusted input payload\n"],
        )

        with pytest.raises(ValueError, match="at least one"):
            promote_git_object_closure(
                quarantine,
                lease_id="missing-include",
                include=(),
                expected_objects={},
            )
        with pytest.raises(ValueError, match="lowercase full"):
            promote_git_object_closure(
                quarantine,
                lease_id="invalid-include",
                include=("--all",),
                expected_objects={},
            )
        with pytest.raises(ValueError, match="lease_id"):
            promote_git_object_closure(
                quarantine,
                lease_id="unsafe\nlease",
                include=(base_commit,),
                expected_objects={base_commit: "commit"},
            )
        idempotent_lease = promote_git_object_closure(
            quarantine,
            lease_id="empty-pack",
            include=(base_commit,),
            exclude=(base_commit,),
            expected_objects={base_commit: "commit"},
        )
        assert len(idempotent_lease.pack_hash) == 40
        with pytest.raises(RuntimeError, match="not tree"):
            promote_git_object_closure(
                quarantine,
                lease_id="invalid-type",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={commit_id: "commit", blob_id: "tree"},
            )

        assert get_git_object_type(commit_id) is None
        assert get_git_object_type(tree_id) is None
        assert get_git_object_type(blob_id) is None


@pytest.mark.parametrize("failed_duplication", (1, 2, 3))
def test_promote_git_object_closure_cleans_up_descriptor_setup_failures(
    temp_git_repo,
    monkeypatch,
    failed_duplication,
):
    """Descriptor exhaustion must not strand a pipe consumer or leak duplicates."""
    with temporary_git_object_environment() as quarantine:
        base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
            quarantine,
            [b"descriptor failure payload\n"],
        )
        real_dup = git_object_promotion.os.dup
        duplicated_descriptors = []
        duplication_count = 0

        def injected_dup(file_descriptor):
            nonlocal duplication_count
            duplication_count += 1
            if duplication_count == failed_duplication:
                raise OSError("injected descriptor exhaustion")
            duplicate = real_dup(file_descriptor)
            duplicated_descriptors.append(duplicate)
            return duplicate

        monkeypatch.setattr(git_object_promotion.os, "dup", injected_dup)

        with pytest.raises(OSError, match="injected descriptor exhaustion"):
            promote_git_object_closure(
                quarantine,
                lease_id=f"descriptor-failure-{failed_duplication}",
                include=(commit_id,),
                exclude=(base_commit,),
                expected_objects={
                    commit_id: "commit",
                    tree_id: "tree",
                    blob_id: "blob",
                },
            )

        assert duplication_count == failed_duplication
        for file_descriptor in duplicated_descriptors:
            with pytest.raises(OSError):
                os.fstat(file_descriptor)
        assert get_git_object_type(commit_id) is None
        assert get_git_object_type(tree_id) is None
        assert get_git_object_type(blob_id) is None


@pytest.mark.parametrize("failed_add", (1, 2))
def test_tracked_pack_pipe_closes_descriptors_when_registration_fails(
    monkeypatch,
    failed_add,
):
    """A BaseException while tracking a pipe must not leak either endpoint."""
    real_pipe = git_object_promotion.os.pipe
    created_descriptors = []

    def recording_pipe():
        descriptors = real_pipe()
        created_descriptors.extend(descriptors)
        return descriptors

    class FailingDescriptorSet(set):
        def __init__(self):
            super().__init__()
            self.add_count = 0

        def add(self, descriptor):
            self.add_count += 1
            if self.add_count == failed_add:
                raise MemoryError("injected descriptor registration failure")
            super().add(descriptor)

    monkeypatch.setattr(git_object_promotion.os, "pipe", recording_pipe)

    with pytest.raises(MemoryError, match="descriptor registration failure"):
        git_object_promotion._open_tracked_pipe(FailingDescriptorSet())

    for descriptor in created_descriptors:
        with pytest.raises(OSError):
            os.fstat(descriptor)


def test_tracked_duplicate_closes_descriptor_when_registration_fails(
    monkeypatch,
):
    """A duplicate remains caller-safe if ownership registration cannot finish."""
    source_descriptor = os.open(os.devnull, os.O_RDONLY)
    real_dup = git_object_promotion.os.dup
    duplicated_descriptors = []

    def recording_dup(descriptor):
        duplicate = real_dup(descriptor)
        duplicated_descriptors.append(duplicate)
        return duplicate

    class FailingDescriptorSet(set):
        def add(self, descriptor):
            raise MemoryError("injected duplicate registration failure")

    monkeypatch.setattr(git_object_promotion.os, "dup", recording_dup)
    try:
        with pytest.raises(MemoryError, match="duplicate registration failure"):
            git_object_promotion._duplicate_tracked_file_descriptor(
                source_descriptor,
                FailingDescriptorSet(),
            )
        os.fstat(source_descriptor)
        for descriptor in duplicated_descriptors:
            with pytest.raises(OSError):
                os.fstat(descriptor)
    finally:
        os.close(source_descriptor)


def test_promote_git_object_closure_has_payload_bounded_python_heap(temp_git_repo):
    """Pack transport should not retain Python memory proportional to blob size."""

    def promotion_peak(size: int) -> int:
        chunk_size = 64 * 1024
        with temporary_git_object_environment() as quarantine:
            base_commit, blob_id, tree_id, commit_id = _quarantined_candidate_commit(
                quarantine,
                (os.urandom(chunk_size) for _ in range(size // chunk_size)),
            )
            gc.collect()
            tracemalloc.start()
            try:
                promote_git_object_closure(
                    quarantine,
                    lease_id=f"heap-{size}",
                    include=(commit_id,),
                    exclude=(base_commit,),
                    expected_objects={
                        commit_id: "commit",
                        tree_id: "tree",
                        blob_id: "blob",
                    },
                )
                return tracemalloc.get_traced_memory()[1]
            finally:
                tracemalloc.stop()

    small_peak = promotion_peak(1024 * 1024)
    large_peak = promotion_peak(16 * 1024 * 1024)

    assert large_peak <= small_peak + 256 * 1024
