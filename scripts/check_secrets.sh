#!/usr/bin/env bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
if git ls-files -z -co --exclude-standard -- . ':!scripts/check_secrets.sh' \
  | xargs -0 grep -nE '(P1S|A1_MINI)_ACCESS_CODE=[0-9a-fA-F]{8}([^0-9a-fA-F]|$)|(P1S|A1_MINI)_SERIAL=[0-9A-F]{15}([^0-9A-F]|$)'; then
  echo "Potential live printer credential or serial found in a non-ignored file." >&2
  exit 1
fi
echo "No credential-shaped printer values found in tracked or untracked non-ignored files."
