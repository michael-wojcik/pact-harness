# pact-harness

A platform-neutral harness that runs [PACT](https://github.com/Synaptic-Labs-AI/PACT-Plugin) on any agentic coding platform.

PACT is a Claude Code plugin: an orchestrator plus twelve specialists, a
PREPARE → ARCHITECT → CODE → TEST cycle, a task board, persistent memory, a
durable journal, and 34 hook registrations across 28 modules. This repository
makes that same core installable elsewhere — starting with DeepSeek Harness —
without forking or patching it.

## How it works

The harness treats Claude Code's hook protocol as the internal ABI:

1. A platform adapter normalises its native event into `pact.event.v1`.
2. The runtime synthesises Claude-shaped stdin and environment, then runs the
   **vendored, unmodified** hook.
3. The hook's stdout and exit code are decoded into `pact.directive.v1`.
4. The adapter renders those directives back into the platform's own decision
   model.

The core stays unmodified, so upstream's own suite remains the regression oracle
and an upstream release is a submodule bump plus a conformance run.

## Install

The harness runs on Python 3.12 or later. From a checkout:

```sh
git clone --recurse-submodules <this repo>
cd pact-harness
pip install -e python
pact doctor
```

### DeepSeek Harness

```sh
pact install dsh --profile pact
dsh --profile pact "implement the feature"
```

That creates a bootable profile from the shipped `headless` template, links the
bridge into it, compiles PACT's personas, commands and skills next to the
profile, and writes a managed block into the profile's patch layer. A fresh
install enforces two gates; see [mode](#enforcement-mode). The profile, the
patch block and the compiled playbook are all removable with
`pact uninstall dsh --profile pact`.

Full quickstart, including how to prove the installation without a model key:
[docs/platforms/dsh.md](docs/platforms/dsh.md).

### MCP platforms

```sh
pact mcp serve
```

Any client that speaks MCP over stdio can call `pact_event`, `pact_gate` and the
memory tools. This is the honest advisory tier: the tools report what PACT
thinks, and nothing on this path can veto a tool call. See
[docs/platforms/mcp.md](docs/platforms/mcp.md).

### Claude Code

Keep using the PACT plugin as it is. The harness does not replace it and does not
intercept it; it ships the same pinned core and a `pact` command that, invoked
bare, still launches `claude --agent PACT:pact-orchestrator` exactly as
upstream's launcher does. See
[docs/platforms/claude-code.md](docs/platforms/claude-code.md).

## Commands

| Command | What it does |
|---|---|
| `pact doctor` | Report what is actually wired: adapter, tier, registry, pin, transport. Exits non-zero when a claim cannot be backed. |
| `pact install dsh` / `pact uninstall dsh` | Install or remove the bridge in a DeepSeek Harness profile. |
| `pact capabilities` | The adapter's declared capability set and derived tier. |
| `pact paths` | Every resolved path, including the upstream-pinned memory location. |
| `pact memory status` | The memory backend's location and whether a store exists (never opens it). |
| `pact conformance` | Run every hook registration against the corpus; all 34 must be covered and understood. |
| `pact upstream inspect / diff / verify / check` | Extract the upstream surface, grade a change L0–L4, check a tree still fits, and report how far the pin is from the latest release. |
| `pact playbook build --platform dsh --out DIR` | Compile personas, commands, skills and protocols for a platform. |
| `pact mcp serve` | Advisory tools over MCP stdio. |
| `pact serve` | The warm hook worker the bridge uses. |

Bare `pact`, or `pact` with flags only, passes through to the upstream Claude
Code launcher. That is deliberate: upstream documents symlinking its own `pact`
onto `PATH`, and a harness that claimed the name and then errored would break
every existing user.

## Enforcement mode

A fresh DeepSeek Harness install runs in `shadow`: every gate computes and
journals its verdict, and only two of them act. `merge_guard_pre` and
`git_commit_check` are enforced from the first session because both read
`tool_input.command`, which DSH supplies under the identical key — no name or
argument mapping sits between the gate and its input. Every other refusing gate
waits until the mapping is proven.

## Measured decisions

- **Latency.** A single file edit runs five hooks. First run without bytecode
  caches: 4.1–4.8 s. Steady state: 201–380 ms. Warm transport: 28–31 ms. See
  [docs/latency.md](docs/latency.md).
- **Counts.** 34 registrations on **13** manifest event names (PACT's model folds
  `PostToolUseFailure` into `PostToolUse`), 28 modules, 3 async.
- **Forking.** A transport that forks must never be called from several threads:
  a child forked from a worker thread can deadlock on a lock held at that
  instant. The runtime enforces this with `supports_parallel`.

## Status

Milestones M0–M5 are complete: contract, adapters, warm transport, DSH bridge,
playbook compiler, MCP adapter, and the upstream compatibility pipeline. M6
(release) is in progress. Two assertions remain unproven: a model-backed
end-to-end turn needs a `DEEPSEEK_API_KEY`, and the `pact_agent` compatibility
tool is not built.

## Development

```sh
git submodule update --init
PYTHONPATH=python/src python3 -m pytest python/tests -q
(cd adapters/dsh && node --test test/)
tools/check-vendor-pin.sh
```

- [docs/plan.md](docs/plan.md) — the approved plan: contract, adapters, milestones.
- [docs/progress.md](docs/progress.md) — where the implementation is, and what is next.
- [docs/architecture.md](docs/architecture.md) — subsystem map.

## Layout

- `vendor/pact-plugin` — PACT, pinned as a git submodule. Never edited.
- `harness/pact-pin.json` — the pin of record; CI compares it against the submodule.
- `harness/upstream-surface.v1.json` — the extracted surface baseline.
- `python/src/pact_harness/` — the runtime.
- `adapters/dsh/` — the cordis bridge.
- `tools/` — repository tooling, including the upstream sync script.

## License

MIT. PACT is MIT and remains the property of its authors; see
`vendor/pact-plugin/LICENSE`.
