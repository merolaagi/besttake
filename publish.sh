#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
REPO="${1:-besttake}"
VER="$(cat VERSION)"

command -v git >/dev/null 2>&1 || { echo "git is required."; exit 1; }
[ -d .git ] || git init -q -b main
git add -A
if ! git diff --cached --quiet; then
  git commit -q -m "BestTake v${VER}"
fi
git rev-parse -q --verify "refs/tags/v${VER}" >/dev/null || git tag "v${VER}"

if ! git remote get-url origin >/dev/null 2>&1; then
  command -v gh >/dev/null 2>&1 || { echo "Install the GitHub CLI first: brew install gh && gh auth login"; exit 1; }
  OWNER="$(gh api user -q .login)"
  if gh repo view "${OWNER}/${REPO}" >/dev/null 2>&1; then
    git remote add origin "https://github.com/${OWNER}/${REPO}.git"
  else
    gh repo create "${REPO}" --public --source=. --remote=origin \
      --description "BestTake: learn anything from the best explanation on YouTube"
  fi
fi

git push -u origin main
git push origin --tags
echo "Published v${VER} to $(git remote get-url origin)"
