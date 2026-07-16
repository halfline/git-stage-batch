"""Git ref namespace constants and trusted batch-ref formatting."""

GIT_STAGE_BATCH_REF_NAMESPACE = "refs/git-stage-batch"
BATCH_CONTENT_REF_PREFIX = f"{GIT_STAGE_BATCH_REF_NAMESPACE}/batches/"
BATCH_STATE_REF_PREFIX = f"{GIT_STAGE_BATCH_REF_NAMESPACE}/state/"
LEGACY_BATCH_REF_PREFIX = "refs/batches/"


def format_batch_content_ref_name(batch_name: str) -> str:
    """Format an already-validated batch name as its authoritative content ref."""
    return f"{BATCH_CONTENT_REF_PREFIX}{batch_name}"


def format_batch_state_ref_name(batch_name: str) -> str:
    """Format an already-validated batch name as its authoritative state ref."""
    return f"{BATCH_STATE_REF_PREFIX}{batch_name}"


def format_legacy_batch_ref_name(batch_name: str) -> str:
    """Format an already-validated batch name as its compatibility content ref."""
    return f"{LEGACY_BATCH_REF_PREFIX}{batch_name}"
