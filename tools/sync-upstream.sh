#!/usr/bin/env bash
# sync-upstream.sh [REF] -- absorb an upstream release, measured.
#
#   REF   a tag (v4.8.36) or commit; default: the latest release tag upstream
#
# What it does, in order:
#   1. snapshots the committed surface manifest
#   2. fetches and checks out REF inside the vendored submodule
#   3. regenerates the manifest from the new tree
#   4. classifies the difference L0-L4, then verifies and re-runs conformance
#   5. rewrites harness/pact-pin.json with the new version, commit and tag
#
# It never commits. The working tree is left with the new gitlink, the
# regenerated manifest and the new pin, so a human reviews exactly what moved.
# Exit status: 0 for L0-L2 with green checks, 3 otherwise.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON="${PACT_PYTHON:-python3}"
SUBMODULE="vendor/pact-plugin"
PIN="harness/pact-pin.json"
MANIFEST="harness/upstream-surface.v1.json"
REF="${1:-}"

die() { echo "sync-upstream: $*" >&2; exit 2; }

command -v git >/dev/null 2>&1 || die "git is required"
"$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)' \
  || die "a Python 3.12+ interpreter is required (set PACT_PYTHON)"

[ -f "$PIN" ] || die "missing $PIN"
[ -f "$MANIFEST" ] || die "missing $MANIFEST (run pact upstream inspect first)"
[ -d "$SUBMODULE" ] || die "missing $SUBMODULE (run git submodule update --init)"

if [ -z "$REF" ]; then
  REF="$(git -C "$SUBMODULE" ls-remote --tags --refs origin \
    | awk -F/ '{print $NF}' \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' \
    | sort -V | tail -1)"
  [ -n "$REF" ] || die "could not resolve the latest upstream tag"
fi

echo "sync-upstream: target $REF"

OLD_MANIFEST="$(mktemp)"
DIFF_JSON="$(mktemp)"
CORPUS_JSON="$(mktemp)"
trap 'rm -f "$OLD_MANIFEST" "$DIFF_JSON" "$CORPUS_JSON"' EXIT
cp "$MANIFEST" "$OLD_MANIFEST"
OLD_VERSION="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1]))["pactVersion"])' "$PIN")"

git -C "$SUBMODULE" fetch --tags --force --quiet origin
git -C "$SUBMODULE" checkout --quiet "$REF"
COMMIT="$(git -C "$SUBMODULE" rev-parse HEAD)"
VERSION="${REF#v}"
echo "sync-upstream: checked out $COMMIT"

PYTHONPATH=python/src "$PYTHON" -m pact_harness upstream inspect --portable --out "$MANIFEST"

STATUS=0
if PYTHONPATH=python/src "$PYTHON" -m pact_harness upstream diff --json \
     "$OLD_MANIFEST" "$MANIFEST" >"$DIFF_JSON"; then
  :
else
  STATUS=3
fi
LEVEL="$("$PYTHON" -c 'import json,sys;print(json.load(open(sys.argv[1]))["level"])' "$DIFF_JSON")"
COUNT="$("$PYTHON" -c 'import json,sys;print(len(json.load(open(sys.argv[1]))["differences"]))' "$DIFF_JSON")"
echo "sync-upstream: difference $LEVEL ($COUNT change(s))"
case "$LEVEL" in
  L0|L1|L2) ;;
  *) STATUS=3 ;;
esac

if ! PYTHONPATH=python/src "$PYTHON" -m pact_harness upstream verify; then
  echo "sync-upstream: verify failed; the new tree needs classification work" >&2
  STATUS=3
fi
if ! PYTHONPATH=python/src "$PYTHON" -m pact_harness conformance; then
  echo "sync-upstream: conformance failed; the decoder does not understand the new tree" >&2
  STATUS=3
fi

# The verdict matrix is the finer-grained signal: conformance asks whether every
# registration is understood, while replay asks whether any of them now answers
# differently on the same frames.
BASELINE="harness/corpus/pact-${OLD_VERSION}.json"
if [ -f "$BASELINE" ]; then
  if PYTHONPATH=python/src "$PYTHON" -m pact_harness corpus replay "$BASELINE" --json \
       >"$CORPUS_JSON" 2>&1; then
    echo "sync-upstream: corpus verdicts unchanged since $OLD_VERSION"
  else
    echo "sync-upstream: corpus verdicts changed since $OLD_VERSION; review" >&2
    tail -c 2000 "$CORPUS_JSON" >&2 || true
    STATUS=3
  fi
fi

"$PYTHON" - "$VERSION" "$COMMIT" "$REF" <<'PY'
import json
import sys
from pathlib import Path

version, commit, tag = sys.argv[1:4]
path = Path("harness/pact-pin.json")
data = json.loads(path.read_text(encoding="utf-8"))
data.update({"pactVersion": version, "commit": commit, "tag": tag})
text = json.dumps(data, indent=2, sort_keys=False)
path.write_text(text + chr(10), encoding="utf-8")
print(f"sync-upstream: pin updated to {tag} ({commit[:12]})")
PY

if [ "$STATUS" -ne 0 ]; then
  echo "sync-upstream: review required (level $LEVEL); nothing was committed" >&2
  exit "$STATUS"
fi
echo "sync-upstream: absorbed; review the diff and commit"