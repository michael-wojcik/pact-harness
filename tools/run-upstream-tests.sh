#!/usr/bin/env bash
# run-upstream-tests.sh [pytest args] -- run PACT's own suite, unmodified.
#
# The vendored tree is never edited, and pytest writes caches next to the tests
# it runs, so the suite runs against a copy. A plain file copy is not enough:
# several upstream tests shell out to git and require a repository with tracked
# files, so the copy is a local clone instead.
#
# The working directory is the plugin subdirectory, because that is where the
# suite lives; the clone root is the repository root, because several tests
# resolve siblings such as scripts/ and .claude-plugin/ relative to it.
#
# Expected: the whole suite passes. A failure here means the harness is pinning
# a tree that does not satisfy its own tests, which is a pin problem, not a
# harness problem.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PACT_PYTHON:-python3}"
SOURCE="$ROOT/vendor/pact-plugin"
PLUGIN="$SOURCE/pact-plugin"

[ -d "$PLUGIN/tests" ] || { echo "run-upstream-tests: $PLUGIN/tests is missing" >&2; exit 2; }
git -C "$SOURCE" rev-parse --git-dir >/dev/null 2>&1 \
  || { echo "run-upstream-tests: $SOURCE is not a git checkout" >&2; exit 2; }
command -v "$PYTHON" >/dev/null 2>&1 || { echo "run-upstream-tests: $PYTHON not found" >&2; exit 2; }

# The vendored conftest refuses an unseeded pytest, so a suite run without the
# plugins it expects dies as an internal error. Name the fix before that happens.
if ! "$PYTHON" -c "import pytest_randomly" >/dev/null 2>&1; then
  echo "run-upstream-tests: pytest-randomly is missing." >&2
  echo "  install upstream's pinned test set:" >&2
  echo "  $PYTHON -m pip install 'pytest==9.2.0' 'pytest-randomly==5.1.0' 'pytest-xdist==3.8.0'" >&2
  exit 2
fi

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
git clone --quiet --no-hardlinks "$SOURCE" "$WORK/repo"
cd "$WORK/repo/pact-plugin"

GAPS_FILE="$ROOT/tools/upstream-known-macos-gaps.txt"
if [ "${PACT_UPSTREAM_MACOS:-0}" = "1" ] && [ -f "$GAPS_FILE" ]; then
  echo "run-upstream-tests: deselecting known macOS platform gaps from $GAPS_FILE"
  while IFS= read -r node_id; do
    case "$node_id" in
      ""|"#"*) continue ;;
    esac
    # pytest resolves --deselect against collected node ids, and the collected
    # form depends on where rootdir landed (the repository root's pyproject, so
    # ids are usually prefixed with the plugin directory). Passing both forms
    # costs nothing: an unmatched deselect is ignored.
    set -- "$@" --deselect "$node_id"
    set -- "$@" --deselect "pact-plugin/$node_id"
  done < "$GAPS_FILE"
fi

echo "run-upstream-tests: upstream suite in $WORK/repo"
"$PYTHON" -m pytest tests -q -p no:cacheprovider "$@"