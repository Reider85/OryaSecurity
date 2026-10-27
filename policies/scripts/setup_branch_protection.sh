#!/usr/bin/env bash
# Apply branch protection to the policies repository (Prompt 27):
#   - main: require 2 approvers + code owner review
#   - main: require signed commits
#
# Usage: setup_branch_protection.sh <owner/repo>
# Example: setup_branch_protection.sh org/llm-security-policies
#
# Requires: gh CLI authenticated with admin rights on the target repo.
#
# NOTE: classic branch protection is available for public repos on the free plan;
# for private repos you need a paid plan or should use Rulesets instead.

set -euo pipefail

REPO="${1:?usage: setup_branch_protection.sh owner/repo}"

echo "Applying branch protection on ${REPO}:main ..."
gh api -X PUT "repos/${REPO}/branches/main/protection" --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": [
      "opa check / test / fmt",
      "Verify commit signatures"
    ]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true,
    "required_approving_review_count": 2
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "required_conversation_resolution": true
}
JSON

echo "Requiring signed commits on ${REPO}:main ..."
gh api -X POST "repos/${REPO}/branches/main/protection/required_signatures" -F signed=true

echo "Done. Verify with:"
echo "  gh api repos/${REPO}/branches/main/protection --jq '.required_pull_request_reviews.required_approving_review_count'"
