# git-stage-batch prompt integration for bash
#
# Shows "BATCH" in your prompt when a git-stage-batch session is active.
#
# Usage:
#   Add to your ~/.bashrc:
#
#     source /usr/share/git-stage-batch/git-stage-batch-prompt.sh
#     PS1='$(__git_ps1 "(%s)")$(__git_stage_batch_ps1) \$ '
#
#   This will show: (main BATCH) $ when a session is active
#

__git_stage_batch_ps1()
{
    local git_dir
    git_dir=$(git rev-parse --git-dir 2>/dev/null) || return

    if [ -f "$git_dir/git-stage-batch/session-state.json" ]; then
        printf " BATCH"
    fi
}
