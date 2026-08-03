#!/usr/bin/env bash
set -e

REPO_DIR="/home/botuser/DiscordBots/BarbaricUtils"
BRANCH="main"
REMOTE="origin"
WATCH_DIR="persistent"

BOT_NAME="github-actions[bot]"
BOT_EMAIL="41898282+github-actions[bot]@users.noreply.github.com"

cd "$REPO_DIR"

# Only run on the expected branch
CURRENT_BRANCH="$(git branch --show-current)"
[ "$CURRENT_BRANCH" = "$BRANCH" ] || exit 0

# Check if persistent/ has changes
if git diff --quiet -- "$WATCH_DIR" && git diff --cached --quiet -- "$WATCH_DIR"; then
    exit 0
fi

# Fetch remote commits
git fetch "$REMOTE"

# Rebase local commits on top of remote safely
if ! git merge-base --is-ancestor "$REMOTE/$BRANCH" HEAD; then
    git pull --rebase --autostash "$REMOTE" "$BRANCH" || exit 0
fi

# Re-check persistent/ after pull
if git diff --quiet -- "$WATCH_DIR" && git diff --cached --quiet -- "$WATCH_DIR"; then
    exit 0
fi

# Stage persistent/
git add "$WATCH_DIR"

# Commit with bot author for this commit only
GIT_COMMITTER_NAME="$BOT_NAME" \
GIT_COMMITTER_EMAIL="$BOT_EMAIL" \
git commit --author="$BOT_NAME <$BOT_EMAIL>" \
           -m "[automatic] Storing persistent data ($(date -I))"

# Push
git push "$REMOTE" "$BRANCH"
