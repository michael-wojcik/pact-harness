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

From PyPI:

```
python -m pip install pact-harness
```

That installs the CLI and the Python library. The hooks themselves are the
vendored PACT tree, which is part of a harness checkout rather than the wheel, so
a bare pip install reports the plugin root as unresolved: use a checkout, or
point `PACT_PLUGIN_ROOT` at a PACT tree. `pip install -e python` installs the CLI and the bridge from source.

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

The bridge bundle is also published on npm as `@pact-harness/dsh`, so a profile can add it
directly with `dsh plugin --profile <name> add @pact-harness/dsh`. `pact install dsh` additionally compiles the playbook
(personas, commands, skills) next to the profile and writes the enforcement block.

Full quickstart, including how to prove the installation without a model key:
[docs/platforms/dsh.md](docs/platforms/dsh.md).

### MCP platforms

```sh
pact mcp serve
```

Any client that speaks MCP over stdio can call `pact_event`, `pact_gate`, the
memory tools and the journal tools. This is the honest advisory tier: the tools
report what PACT thinks, and nothing on this path can veto a tool call. See
[docs/platforms/mcp.md](docs/platforms/mcp.md).

Platforms that read an `AGENTS.md` file can also have the policy and the skills
installed into the project:

```sh
pact install generic --target .
```

See [docs/platforms/generic.md](docs/platforms/generic.md).

### Claude Code

The default is unchanged: keep using the PACT plugin as it is. The harness does
not replace it and does not intercept it; it ships the same pinned core and a
`pact` command that, invoked bare, still launches
`claude --agent PACT:pact-orchestrator` exactly as upstream's launcher does.

There is also an opt-in bridged mode that routes every hook through the harness,
gated on a byte-parity check across the 48-cell corpus:

```sh
pact install claude-code --mode bridged --target ~/.pact/claude-bridged
```

See [docs/platforms/claude-code.md](docs/platforms/claude-code.md).

## Commands

| Command | What it does |
|---|---|
| `pact doctor` | Report what is actually wired: adapter, tier, registry, pin, transport. Exits non-zero when a claim cannot be backed. |
| `pact install dsh` / `pact uninstall dsh` | Install or remove the bridge in a DeepSeek Harness profile. |
| `pact install … --venv` | Also create the memory CLI's virtual environment under the state home. |
| `pact install generic` / `pact uninstall generic` | Write the managed `AGENTS.md` section and link the skills for an advisory platform. |
| `pact install claude-code` | Native mode (nothing to install) or `--mode bridged --target DIR`, gated on byte parity across the 48-cell corpus. |
| `pact hook HOOK` | Run one hook against one frame and print what it did (add `--raw` for the process result). |
| `pact migrate-state --to DIR` | Move a state root, refusing a partial move. |
| `pact capabilities` | The adapter's declared capability set and derived tier. |
| `pact paths` | Every resolved path, including the upstream-pinned memory location. |
| `pact memory status` | The memory backend's location and whether a store exists (never opens it). |
| `pact conformance` | Run every hook registration against the corpus; all 34 must be covered and understood. |
| `pact corpus harvest / replay` | Record the corpus verdict matrix for a tree, then replay it against another to name what moved. |
| `pact upstream inspect / diff / verify / check` | Extract the upstream surface, grade a change L0–L4, check a tree still fits, and report how far the pin is from the latest release. |
| `pact dev link --plugin DIR` / `pact dev unlink` | Run against a working checkout instead of the pin; `doctor` reports the override and whether that tree is dirty. |
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

Milestones M0–M6 are implemented: contract, adapters, warm transport, DSH
bridge, playbook compiler, MCP adapter, the upstream compatibility pipeline, and
the release surface. v0.1.6 is published on GitHub, PyPI and npm.

Four lanes guard it: contract (push, PR and tag: tests, pin invariant and
upstream's own suite), release (tags: build and publish to PyPI and npm over
OIDC), model-nightly (a real model-backed DSH session), and upstream (daily:
upstream main still fits, plus a release radar that turns red when a PACT release
is waiting and reports its L-level and pre-qualification evidence).

The last assertion -- a live model-backed turn with the bridge mounted -- is
covered by `.github/workflows/model-nightly.yml`, which installs DSH from npm on an
Ubuntu runner and runs `tools/model-lane.sh` with the repository's
`DEEPSEEK_API_KEY` secret. First successful run: `dsh` exited 0, 223 events,
no error events, and the bridge wrote its activation record.

## Development

```sh
git submodule update --init
PYTHONPATH=python/src python3 -m pytest python/tests -q
(cd adapters/dsh && node --test test/)
tools/check-vendor-pin.sh
```

To run against a working checkout instead of the pin — for testing an upstream
change before it is tagged — link it once:

```sh
pact dev link --plugin ../PACT-Plugin
pact doctor            # plugin root: ... (dev-link), reports a dirty tree
pact dev unlink
```

Precedence is `PACT_PLUGIN_ROOT` (one command), then the link, then the pin. A
link whose tree has moved or been deleted is ignored rather than breaking every
command, and `doctor` warns when the tree in use has uncommitted changes.

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

## Publishing

A tag whose version matches every manifest triggers the release lane: both test
suites, the vendor-pin check, the build, a GitHub release, and optionally PyPI
and npm.

- **npm**: the bridge is published by the release lane with trusted publishing
  (OIDC), no stored token, under the `@pact-harness` scope. Register a trusted publisher on
  npmjs.com for `@pact-harness/dsh` (GitHub Actions, owner `michael-wojcik`, repository `pact-harness`, workflow
  `release.yml`, environment `npm`, direct publish allowed), then set the repository
  variable `NPM_PUBLISH=true`. The first publish must follow within two days of registering.
- **PyPI**: register a pending trusted publisher on PyPI (project
  `pact-harness`, owner `michael-wojcik`, repository `pact-harness`, workflow `release.yml`,
  environment `pypi`), then set the repository variable `PYPI_PUBLISH=true`. The lane exchanges
  OIDC for a short-lived token, so no PyPI secret is stored.

## License

MIT; see [LICENSE](LICENSE). PACT is MIT and remains the property of its authors;
see `vendor/pact-plugin/LICENSE`.
