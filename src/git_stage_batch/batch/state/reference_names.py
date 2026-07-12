"""Git ref namespace constants for batch storage."""

GIT_STAGE_BATCH_REF_NAMESPACE = "refs/git-stage-batch"
BATCH_CONTENT_REF_PREFIX = f"{GIT_STAGE_BATCH_REF_NAMESPACE}/batches/"
BATCH_STATE_REF_PREFIX = f"{GIT_STAGE_BATCH_REF_NAMESPACE}/state/"
LEGACY_BATCH_REF_PREFIX = "refs/batches/"
