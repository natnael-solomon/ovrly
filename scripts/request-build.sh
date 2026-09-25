#!/usr/bin/env sh
# Request a production build of the current origin/main through the Telegram APK workflow.
# Resolves the exact commit for you, shows it, asks once, then dispatches. Nothing else.
set -eu
cd "$(dirname "$0")/.."

git fetch --quiet origin main
sha=$(git rev-parse origin/main)
if [ "${#sha}" -ne 40 ] || [ -n "$(printf '%s' "$sha" | tr -d '0-9a-f')" ]; then
  echo "Could not resolve origin/main to a commit" >&2; exit 1
fi
subject=$(git log -1 --format=%s "$sha")
printf 'Requesting build of origin/main:\n  %s\n  %s\n' "$sha" "$subject"
if [ "${1:-}" != "--yes" ]; then
  printf 'Dispatch Telegram APK for this commit? [y/N] '
  read -r answer
  case "$answer" in y|Y) ;; *) echo 'Cancelled.'; exit 1;; esac
fi
gh workflow run telegram-apk.yml --ref main -f "source_sha=$sha"
echo 'Dispatched. The owner will be asked to approve once the unsigned build completes.'
