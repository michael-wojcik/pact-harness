# pact-harness

A platform-neutral harness that runs [PACT](https://github.com/Synaptic-Labs-AI/PACT-Plugin) on any agentic coding platform.

PACT is a Claude Code plugin today: an orchestrator plus twelve specialists, a PACT cycle, a task board, persistent memory, a durable journal, and 34 hook registrations across 28 modules. This repository makes that same core installable on other agent platforms — starting with DeepSeek Harness — without forking or patching it.

## How it works

The harness treats Claude Code's hook protocol as the internal ABI:

1. A platform adapter normalises its native event into `pact.event.v1`.
2. The runtime synthesises Claude-shaped stdin and environment, then runs the **vendored, unmodified** hook.
3. The hook's stdout and exit code are decoded into `pact.directive.v1`.
4. The adapter renders those directives back into the platform's own decision model.

Because the core is unmodified, upstream's test suite stays the regression oracle and an upstream release is a submodule bump plus a conformance run.

## Status

Milestone M1 in progress. The contract, capability model, vendor pin, CLI, hook
registry, output decoder and the Claude Code adapter exist. A real vendored gate
(`merge_guard_pre`) refuses a real force-push through the harness, and
`pact doctor --adapter claude-code` reports the live registry and the reference
tier. The DeepSeek Harness adapter is not implemented yet, so `pact doctor`
reports tier T0 for it and says so.

Measured decisions worth knowing before extending this:

- **Latency.** A single file edit runs five hooks. First run without bytecode
  caches: 4.1-4.8 s. Steady state: 201-380 ms. With the warm transport: 28-31 ms.
  The warm transport is the better default — 7-10x in steady state, and it
  removes the first-run cost that a read-only vendored tree pays every time. See
  [docs/latency.md](docs/latency.md).
- **Counts.** Upstream's manifest registers 34 hooks on **13** event names
  (PACT's own model folds `PostToolUseFailure` into `PostToolUse`), across 28
  modules; **three** are async, not four.

## Plan and progress

- [docs/plan.md](docs/plan.md) — the approved plan: contract, adapters, milestones.
- [docs/progress.md](docs/progress.md) — where the implementation is, and what is next.

## Layout

- `vendor/pact-plugin` — PACT, pinned as a git submodule at `v4.8.36` (`4607c7a`). Never edited.
- `harness/pact-pin.json` — the pin of record; CI compares it against the submodule.
- `python/src/pact_harness/` — the harness runtime.
- `python/tests/` — harness tests (not the upstream suite).
- `tools/` — repository tooling.

## Development

```sh
git submodule update --init
PYTHONPATH=python/src python3 -m pytest python/tests -q
PYTHONPATH=python/src python3 -m pact_harness doctor
```

Requires Python 3.12 or later (upstream's floor). 3.13 and later are permitted but marked unverified, because upstream CI certifies only 3.12.

## License

MIT. PACT is MIT and remains the property of its authors; see `vendor/pact-plugin/LICENSE`.
