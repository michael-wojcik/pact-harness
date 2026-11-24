# Architecture

Four layers, each with one job.

```text
platform
  │  native event
  ▼
adapter          normalise → pact.event.v1          render ← pact.directive.v1
  ▼
runtime          synthesise stdin + env, run the vendored hook, decode its output
  ▼
vendored PACT    unmodified, pinned, the only policy implementation
```

## Subsystems

| Path | Responsibility |
|---|---|
| `python/src/pact_harness/contract/` | `pact.event.v1` and `pact.directive.v1`: hand-validated models plus published JSON Schemas |
| `python/src/pact_harness/runtime/` | Hook registry, child environment, cold and warm transports, the decoder, and the per-event runner |
| `python/src/pact_harness/adapters/` | The adapter protocol and three implementations: Claude Code, DeepSeek Harness, and the portable MCP adapter |
| `python/src/pact_harness/engine.py` | Resolve, run and fold: several hook outcomes become one decision |
| `python/src/pact_harness/playbook/` | Compile personas, commands, skills and protocols for a platform |
| `python/src/pact_harness/upstream/` | Extract the surface, classify a change L0–L4, verify a tree still fits, report release distance |
| `python/src/pact_harness/mcp/` | Advisory tools over MCP stdio |
| `python/src/pact_harness/install/` | Put the bridge into a platform profile, reversibly |
| `adapters/dsh/` | The cordis bridge: extension points in, decisions out |
| `vendor/pact-plugin/` | PACT itself. Never edited. |

## Data flow for one tool call

1. DSH fires `tools/pre-execute`.
2. The bridge builds a `pact.event.v1` event: session, actor, canonical tool name,
   normalised arguments.
3. It sends the event to the warm worker, which imports PACT's shared tree once
   and forks a child per hook.
4. Each child runs with a synthesised Claude-shaped frame and environment.
5. Their stdout and exit codes are decoded into directives, folded into one
   decision, and returned.
6. The bridge applies its enforcement policy and returns a `PreToolDecision`.

## Invariants

- **The vendored tree is never edited.** `tools/check-vendor-pin.sh` checks both
  the recorded pin and the stored gitlink.
- **Capabilities are declared, never inferred.** A tier is derived from a
  capability set; `pact doctor` refuses to claim what is not wired.
- **A decode failure is never an allow.** An unrecognised decision fails closed
  on veto-capable events.
- **A forking transport is never called from several threads.** Forking from a
  worker thread can deadlock the child; the runtime checks
  `supports_parallel`.
