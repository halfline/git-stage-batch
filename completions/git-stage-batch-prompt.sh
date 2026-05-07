# git-stage-batch prompt integration for bash
#
# Shows "STAGING" in your prompt when a git-stage-batch session is active.
#
# Usage:
#   Add to your ~/.bashrc:
#
#     source /usr/share/git-stage-batch/git-stage-batch-prompt.sh
#     PS1='$(__git_ps1 "(%s)")$(__git_stage_batch_ps1) \$ '
#
#   This will show: (main STAGING) $ when a session is active
#
#   Pass a custom format to include your own spacing, brackets, or fields:
#
#     PS1='$(__git_ps1 "(%s)")$(__git_stage_batch_ps1 " [{status} {processed}/{total}]") \$ '
#

__git_stage_batch_ps1()
{
    local format

    format=${1:-" STAGING"}
    git-stage-batch status --for-prompt="$format" 2>/dev/null
}
