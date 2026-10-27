#!/usr/bin/env bash
# Publish policies/ as a standalone GitHub repository (Prompt 27):
#   github.com/org/llm-security-policies
#
# Usage: policies/scripts/publish_repo.sh [git-remote-url]
# Default remote: git@github.com:org/llm-security-policies.git
#
# Method: git subtree split of the policies/ prefix into an orphan branch,
# then push that branch as `main` of the new repository. The split branch is
# local-only and can be deleted afterwards.

set -euo pipefail

REMOTE_URL="${1:-git@github.com:org/llm-security-policies.git}"
SPLIT_BRANCH="policies-split-publish"

cd "$(git rev-parse --show-toplevel)"

if [ ! -d policies ]; then
    echo "policies/ not found — run this from the OryaSecurity monorepo." >&2
    exit 1
fi

echo "Splitting policies/ into branch ${SPLIT_BRANCH} ..."
git subtree split --prefix=policies -b "${SPLIT_BRANCH}"

echo "Pushing ${SPLIT_BRANCH} -> ${REMOTE_URL} (main) ..."
echo "The target repository must already exist (create it with: gh repo create ...)."
git push "${REMOTE_URL}" "${SPLIT_BRANCH}:main"

echo
echo "Next steps in the standalone repo:"
echo "  1. git clone ${REMOTE_URL} && cd llm-security-policies"
echo "  2. git tag v1.0.0 && git push origin v1.0.0   # triggers bundle + cosign CI"
echo "  3. scripts/setup_branch_protection.sh org/llm-security-policies"
echo "  4. Add the SSH signing key (Settings -> SSH keys -> Signing Key) for every committer"
echo "  5. Cleanup in monorepo: git branch -D ${SPLIT_BRANCH}"
