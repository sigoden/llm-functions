#!/usr/bin/env bash
set -euo pipefail

# Helper script to create/update and push the spammedchat branch.
# It ensures the local spammedchat branch is up-to-date with the current branch
# (assumed to be the arena branch) and then pushes it to origin/spammedchat.
# If the remote branch exists and has diverged, it refuses to push unless FORCE=1 is set.

# Configuration
REMOTE="origin"
LOCAL_BRANCH="spammedchat"
# We assume the current branch is the one we want to push from (e.g., arena/01a07531-llm-functions)
# But we can also allow specifying a source branch via environment variable.
SOURCE_BRANCH="${SOURCE_BRANCH:-$(git rev-parse --abbrev-ref HEAD)}"

echo "Current branch: $SOURCE_BRANCH"
echo "Target branch: $LOCAL_BRANCH"
echo "Remote: $REMOTE"

# Ensure we are in a git repository
if ! git rev-parse --git-dir > /dev/null 2>&1; then
    echo "Error: Not a git repository."
    exit 1
fi

# Fetch latest from remote to know the state of origin/spammedchat
echo "Fetching from $REMOTE..."
git fetch "$REMOTE"

# Check if local spammedchat branch exists
if git show-ref --verify --quiet "refs/heads/$LOCAL_BRANCH"; then
    echo "Local branch '$LOCAL_BRANCH' exists. Resetting to $SOURCE_BRANCH..."
    git checkout "$LOCAL_BRANCH"
    git reset --hard "$SOURCE_BRANCH"
else
    echo "Local branch '$LOCAL_BRANCH' does not exist. Creating from $SOURCE_BRANCH..."
    git checkout -b "$LOCAL_BRANCH" "$SOURCE_BRANCH"
fi

# Now check the remote branch
if git show-ref --verify --quiet "refs/remotes/$REMOTE/$LOCAL_BRANCH"; then
    echo "Remote branch $REMOTE/$LOCAL_BRANCH exists."
    # Check if the remote branch is an ancestor of the local branch (i.e., we can fast-forward)
    if git merge-base --is-ancestor "$REMOTE/$LOCAL_BRANCH" "$LOCAL_BRANCH"; then
        echo "Remote branch is behind or equal to local branch. Fast-forward push possible."
        CAN_FAST_FORWARD=true
    else
        echo "Remote branch has diverged from local branch."
        if [[ "${FORCE:-}" == "1" ]]; then
            echo "FORCE=1 set, allowing forced push."
            CAN_FAST_FORWARD=false
        else
            echo "Error: Remote branch has diverged. Set FORCE=1 to overwrite."
            exit 1
        fi
    fi
else
    echo "Remote branch $REMOTE/$LOCAL_BRANCH does not exist. Will create on push."
    CAN_FAST_FORWARD=true
fi

# Push
if [[ "${CAN_FAST_FORWARD:-}" == "true" ]]; then
    echo "Pushing $LOCAL_BRANCH to $REMOTE..."
    git push "$REMOTE" "$LOCAL_BRANCH"
else
    echo "Force-pushing $LOCAL_BRANCH to $REMOTE..."
    git push --force "$REMOTE" "$LOCAL_BRANCH"
fi

echo "Push completed."
echo "Resulting history of $REMOTE/$LOCAL_BRANCH:"
git log --oneline --graph -5 "$REMOTE/$LOCAL_BRANCH"
