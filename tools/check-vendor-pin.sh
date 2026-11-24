#!/usr/bin/env bash
# Assert the vendored PACT tree is exactly the pinned commit.
#
# Two facts, not one: the recorded pin, and the gitlink the superproject stores
# for the submodule. A commit made inside the submodule, or a moved gitlink,
# fails this check rather than silently changing which PACT the harness runs.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PACT_PYTHON:-python3}"

if ! "$PYTHON" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)'; then
  echo "check-vendor-pin: $PYTHON is below the 3.12 floor" >&2
  exit 127
fi

PYTHONPATH="$ROOT/python/src${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" - "$ROOT" <<'PY'
import sys

from pact_harness.paths import find_harness_root
from pact_harness.vendor import inspect_vendor, load_pin

root = find_harness_root()
if root is None:
    print("check-vendor-pin: harness root not found", file=sys.stderr)
    raise SystemExit(1)

pin = load_pin(root)
state = inspect_vendor(root, pin)

if not state.exists:
    print(f"check-vendor-pin: vendored tree missing: {state.path}", file=sys.stderr)
    raise SystemExit(1)

if not state.at_pin:
    print("check-vendor-pin: vendored tree is not at the pin", file=sys.stderr)
    for problem in state.problems:
        print(f"  - {problem}", file=sys.stderr)
    raise SystemExit(1)

tag = pin.tag or "(no tag)"
print(f"check-vendor-pin: OK  PACT v{pin.pact_version} {tag} {pin.commit[:12]}")
PY
