# Progress

Updated as milestones land. This file is the handover point: a fresh session
reads this and [plan.md](plan.md) before touching code.

## Where we are

- **M0 — complete.** Contract v1, capability model, vendor pin, CLI, doctor, CI.
- **M1 — partially complete.** Registry, decoder, child environment, launcher,
  transport seam, per-group concurrency, Claude Code adapter, and a real
  refusal proven end to end. Remaining: the conformance corpus and the 34/34
  registration parity sweep.
- **M2 — in progress.** Warm transport done. The event engine, directive
  folding and the Python half of the DSH adapter are done; `pact event` is the
  process boundary the cordis plugin will call. Remaining: the bridge itself,
  install, and the end-to-end harness.

## Next actions, in order

1. ~~Warm transport~~ — done (`runtime/warm.py`, `pact serve`).
2. **DSH cordis bridge** (`adapters/dsh/`, TypeScript): map DSH extension points
   to `pact.event.v1`, call `pact event`, render directives into DSH decisions,
   register commands, persona section and skill provider.
3. **Conformance corpus** (`conformance/corpus/`): fixtures from
   `vendor/pact-plugin/pact-plugin/tests/fixtures/role_frames.py` plus a
   synthetic frame per event and per role.
4. **34/34 parity sweep**: for every registration, run the hook directly and
   through the harness, and assert identical stdout and exit code.

## Measured facts that corrected the plan

| Claim in the plan | Measured |
|---|---|
| 34 registrations across 12 events | **13** event names in `hooks.json`; PACT's own model has 12 because it folds PostToolUseFailure into PostToolUse |
| four async hooks | **three**: `pin_marker_writer`, `session_end`, `file_tracker` |
| ~0.7 s for a three-hook event | first run without bytecode cache: **4.1–4.8 s**; steady state: **201–380 ms**; warm: **28–31 ms** |
| warm transport is a Phase-E optimization | it is still the better default: 7–10x in steady state, and it removes the first-run penalty a read-only tree always pays |

## Standing conventions

- `vendor/pact-plugin` is never edited. `tools/check-vendor-pin.sh` enforces it.
- One commit per slice, tests green before the commit.
- `pact doctor` must claim only what is wired. If a capability is not proven,
  the tier says so.
- New measured facts go in a test or in this file, not only in a commit message.

## Verify

```sh
PYTHONPATH=python/src python3 -m pytest python/tests -q
tools/check-vendor-pin.sh
PYTHONPATH=python/src python3 -m pact_harness doctor --adapter claude-code
```
