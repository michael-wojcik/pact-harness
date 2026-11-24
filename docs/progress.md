# Progress

Updated as milestones land. This file is the handover point: a fresh session
reads this and [plan.md](plan.md) before touching code.

## Where we are

- **M0 — complete.** Contract v1, capability model, vendor pin, CLI, doctor, CI.
- **M1 — complete.** Registry, decoder, child environment, launcher, transport
  seam, concurrency, Claude Code adapter, and a real refusal proven end to end.
  The conformance suite exercises **all 34 registrations** across 24 corpus
  frames and asserts two things per registration: the adapter re-delivers the
  original frame unchanged, and the hook's output decodes with no problem, no
  fatal decode error, and a refusal whenever the hook exits 2. Judgement is
  covered by negative controls (refusal without a directive, decode problem,
  fatal error, bad exit, launch failure, timeout).
- **M2 — complete except one assertion.** Warm transport, event engine,
  directive folding, the Python DSH adapter, the cordis bridge,
  install/uninstall, and a real DSH profile that composes and boots are done.
  The only unproven assertion is a model-backed turn, which needs a key.
- **M4 — in progress.** The generic MCP adapter serves newline-delimited
  JSON-RPC on stdio: initialize, ping, tools/list and tools/call for
  `pact_event`, `pact_gate` and `pact_status`. It is the honest T0 — the tools
  say "advisory only" in their text, and `pact_status` reports tier T0 and the
  live registry. Remaining: memory and journal tools over the vendor CLIs.
- **M3 — in progress.** The playbook compiler extracts 13 personas, 13 commands
  (Telegram excluded), 21 skills, 22 protocols and 3 references, resolves every
  plugin-relative reference to an absolute path, and replaces Claude's
  `$ARGUMENTS` with the platform placeholder. The installer compiles it next to
  the profile and the bridge registers the orchestrator persona as a system
  prompt section at order 0, registers the 13 `/pact-*` commands (handlers
  inject the compiled body with the user's text substituted), registers the 21
  skills as a zero-copy provider, and gives a teammate agent its specialist
  persona on creation. Remaining: a `pact_agent` compatibility tool so a spawn
  that passes `subagent_type` instead of `name` still selects the right persona.

### DSH end-to-end evidence

- `pact install dsh --profile pact-e2e` ran `dsh plugin add`, which created the
  profile and linked the bundle; `dsh --dump-config` then showed the
  `pact-dsh` row with the enforced-gate config.
- Booting that profile wrote `~/.pact/pact-harness/dsh-activation.json`
  (mode shadow, the two command gates), which proves the plugin loaded and
  `apply()` ran. The run then stopped at `MISSING_CREDENTIAL`, which is the
  expected outcome on a machine with no `DEEPSEEK_API_KEY`; that is the only
  assertion a model-backed run would add.
- A real gate refuses a real command through the bridge's worker:
  `merge_guard_pre` denies `git push --force origin main` and carries its rule
  and reason into the envelope (Node test, ~295 ms).
- The profile was removed after measurement.

## Next actions, in order

1. ~~Warm transport~~ — done (`runtime/warm.py`, `pact serve`).
2. ~~DSH cordis bridge~~ — done (`adapters/dsh/`): events, tool map, decisions,
   warm-worker client, shadow/enforce policy, persona section, 33 Node tests.
3. **M3 playbook compiler**: extract personas, commands, skills and protocols
   from the vendored tree, rewrite what does not survive a platform change, and
   emit per-platform artifacts. The bridge then registers the orchestrator
   persona section and the `/pact-*` commands.
4. **Conformance corpus** (`conformance/corpus/`): fixtures from
   `vendor/pact-plugin/pact-plugin/tests/fixtures/role_frames.py` plus a
   synthetic frame per event and per role.
5. **34/34 parity sweep**: for every registration, run the hook directly and
   through the harness, and assert identical stdout and exit code.

## Defects found by measuring, not by reading

| Symptom | Cause | Fix |
|---|---|---|
| Decisions silently missing from a forked hook | `os._exit` skips interpreter shutdown, discarding buffered stdout | flush before exit |
| Decisions written into the embedder's buffer | the child inherited the parent's `sys.stdout` object | rebind the child's stdio to its descriptors |
| A caller timeout with no error | a pipe's EOF waits on every process holding the write end | temp file plus polled `waitpid` |
| An evaluation hung about 1 in 5 | forking from thread-pool workers deadlocks the child on a lock held at fork time | `supports_parallel` is false for forking transports |

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
