#!/usr/bin/env bash
# model-lane.sh [PROFILE] [PROMPT] -- run one real model-backed PACT session.
#
# This is the assertion the rest of the harness cannot prove: that a live model
# turn runs with PACT's bridge mounted. It needs DEEPSEEK_API_KEY in the
# environment and a profile with the bridge installed; the CI lane prepares both.
#
# The run's events and the bridge's activation record are copied to
# .model-lane/ so a failure can be read after the fact. Exit status is 0 only
# when dsh exited 0, emitted no error event, and the activation record exists.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE="${1:-pact}"
PROMPT="${2:-Reply with exactly: PACT-OK}"
PYTHON="${PACT_PYTHON:-python3}"
OUT="${MODEL_LANE_OUT:-$ROOT/.model-lane}"

if [ -z "${DEEPSEEK_API_KEY:-}" ]; then
  echo "model-lane: DEEPSEEK_API_KEY is not set; a model-backed turn needs one" >&2
  exit 2
fi
if ! command -v dsh >/dev/null 2>&1; then
  echo "model-lane: dsh is not on PATH" >&2
  exit 2
fi

rm -rf "$OUT"
mkdir -p "$OUT"

set +e
dsh --profile "$PROFILE" --json "$PROMPT" > "$OUT/run.json" 2> "$OUT/run.err"
rc=$?
set -e
echo "model-lane: dsh exited $rc"
if [ -s "$OUT/run.err" ]; then
  tail -5 "$OUT/run.err" >&2 || true
fi

ACTIVATION="${DSH_HOME:-$HOME/.dsh}/../.pact/pact-harness/dsh-activation.json"
if [ -f "$HOME/.pact/pact-harness/dsh-activation.json" ]; then
  cp "$HOME/.pact/pact-harness/dsh-activation.json" "$OUT/activation.json"
fi

"$PYTHON" - "$OUT/run.json" "$rc" "$OUT" <<'PY'
import json
import shutil
import sys
from pathlib import Path

run_path, rc, out_dir = Path(sys.argv[1]), int(sys.argv[2]), Path(sys.argv[3])
events = []
for line in run_path.read_text(errors="replace").splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        events.append(json.loads(line))
    except json.JSONDecodeError:
        continue

final = next((event for event in reversed(events) if event.get("type") == "final"), None)
errors = [event for event in events if (event.get("reason") or {}).get("kind") == "error"]
activation = Path.home() / ".pact" / "pact-harness" / "dsh-activation.json"

print(f"model-lane: events={len(events)} final={bool(final)} errors={len(errors)}")
print(f"model-lane: activation record={activation.is_file()}")

# Keep the newest session transcript too: it is the only place a human can read
# what the model actually saw and answered.
sessions = Path.home() / ".dsh" / "sessions"
candidates = sorted(sessions.glob("*.jsonl")) if sessions.is_dir() else []
if candidates:
    newest = candidates[-1]
    shutil.copy(newest, out_dir / "session.jsonl")
    print(f"model-lane: transcript={newest.name}")

failures = []
if rc != 0:
    failures.append(f"dsh exited {rc}")
if errors:
    message = (errors[-1].get("reason") or {}).get("error", {}).get("message", errors[-1])
    failures.append(f"run reported an error: {message}")
if not activation.is_file():
    failures.append("the bridge did not write an activation record")
if failures:
    for failure in failures:
        print(f"model-lane: FAILED: {failure}", file=sys.stderr)
    raise SystemExit(1)
print("model-lane: OK")
PY
