# pact-harness: implementation plan

Status: approved. This is the source of truth for scope and design decisions.
Progress is tracked in [progress.md](progress.md); the measured numbers that
changed parts of this plan are recorded there and in [latency.md](latency.md).

---


## Goal

Build **pact-harness**: one installable harness that brings PACT's orchestration model (orchestrator + 12 specialists, the PREPARE → ARCHITECT → CODE → TEST cycle, task board, handoffs, persistent memory, durable journal, and all 34 hook registrations across 28 hook modules) to **any** agentic coding platform — with Claude Code behavior preserved and DeepSeek Harness (DSH) as the first-class non-Claude target. PACT stops being "a Claude Code plugin" and becomes a portable layer a host platform mounts.

**Success criteria**

1. On DSH, a user installs PACT with one command, opens a session, runs `/pact-orchestrate <task>`, and gets the same gates, journal events, memory writes, specialist delegation and skills a Claude Code user gets today.
2. On Claude Code, a user sees **zero behavior change** unless they opt into bridged mode.
3. A platform with no interception hooks (Cursor, Codex CLI, Gemini CLI, Zed, custom agents) can still run PACT in a declared, honest **advisory** mode via MCP + `AGENTS.md` + the `pact` CLI.
4. The harness **never claims a capability it has not wired**: `pact doctor` prints the tier, per-gate applicability and evidence, and enforce mode refuses to boot when a required capability is missing.
5. **Upstream keeps moving, and the harness keeps up by measurement.** PACT-Plugin stays a Claude Code plugin that its author keeps changing. The harness consumes it through a mechanically extracted *upstream surface*, classifies every bump, and either absorbs it automatically or fails with a precise impact list — never a silent under-enforcement.
6. Upstream PACT stays a pinned dependency, not a fork: bumping PACT is a version bump plus a conformance run, not a re-port.

---

## Confirmed decisions

| # | Decision | Value |
|---|---|---|
| 1 | Identity | GitHub `michael-wojcik/pact-harness`; PyPI `pact-harness`; import `pact_harness`; npm `@pact-harness/dsh` (org to be created); MIT; public |
| 2 | CLI | The harness owns `pact`. A bare `pact`, or one with flags only, passes through to `claude --agent PACT:pact-orchestrator` exactly as today; subcommands are new surface; an unknown subcommand gets a suggestion. Retiring upstream's `bin/pact` is a separate later PR, not a harness dependency |
| 3 | Python | Resolve `PACT_PYTHON` → `python3` if ≥3.12 → DSH's bundled 3.12. Refuse <3.12, warn >3.12. Start simple: `doctor` detects PEP 668 and app-bundle interpreters, and `pact install --venv` exists from day one; the venv is promoted to default only if testing shows failures |
| 4 | DSH persona | Register `ctx.systemPrompt.section`; `--persona=agents-md` is opt-in; the agent-preset route is deferred. `pact persona show` and a hash in `doctor` keep it inspectable |
| 5 | Commands | **13**, all except `telegram-setup`; `peer-review` is M3's final acceptance item. **Telegram is excluded entirely**: no `telegram/` port, no `pact-telegram` MCP entry |
| 6 | Enforcement on DSH | Enforce `merge_guard_pre` and `git_commit_check` from the first session — both read `tool_input.command`, which DSH supplies under the identical key. Shadow the other seven refusing gates until M2 proves the name and argument mapping |
| 7 | State and memory | Separate session roots: `~/.claude` for Claude Code, `~/.pact` for DSH and generic. Memory stays where upstream pins it, reached only through a `MemoryBackend` seam; `pact memory status --json` is the contract; no harness-side memory cache; `PACT_TEST_MEMORY_DIR` is never repurposed |
| 8 | Upstream changes | Run a swapped-in PACT-Plugin folder and warn when it is untested, so integration can be exercised before either repo is tagged. The harness follows **every** upstream release, and a harness release is gated on conformance passing. `pact upstream verify` is the hard gate for CI; a nightly lane tests upstream `main`; `--require-tested-envelope` provides the strict posture on demand |
| 9 | CI model runs | Every PR runs the no-model end-to-end half (activation, journal, skill catalog, command registration, a proven deny). A nightly job with a `DEEPSEEK_API_KEY` secret runs the full model turn |
| 10 | Upstream repository | **Not modified.** The harness repository's nightly lane watches upstream `main` and opens a PR in the harness on drift |

---

## What we measured first (the facts this plan rests on)

| Fact | Measurement |
|---|---|
| Upstream size | 122 non-test Python files / 89,698 lines; 552 test files; 28,619 tests / 27 skips on Python 3.12 (upstream CI's own record) |
| Hook surface | `hooks/hooks.json` = **34 registrations across 12 events → 28 unique modules** (not the "27 hooks" the README implies) |
| Hook runtime feasibility | A vendored hook runs **unmodified** under a synthesized env: `worktree_guard.py` 0.31 s, `git_commit_check.py` 0.63 s, emitting `{"suppressOutput": true}` |
| Coupling seams | `CLAUDE_CONFIG_DIR` has **one** resolver (`shared/paths.get_claude_config_dir`); `CLAUDE_PROJECT_DIR` has **13 direct reads across 11 functions**; **no shared stdin reader** — every entry point opens stdin itself |
| Claude-only vocabulary | `bootstrap_gate` classifies tools by literal names in three frozensets (`_BLOCKED_TOOLS`, `_READ_ONLY_TOOLS`, `_DEGRADED_ASK_TOOLS`); all matchers are literal event/tool strings |
| Captured ABI corpus | `tests/fixtures/role_frames.py` already ships **verbatim captured Claude Code frames** for the 12 events and 3 roles |
| DSH build | The installed app is **0.2.2-rc.5**. The profile `node_modules/@deepseek-ai/*` symlinks point at a stale 0.1.1 npx cache — the adapter must be written against 0.2.2 sources |
| DSH Python | The app ships **Python 3.12.14** at `Resources/runtime/primary-runtime/dependencies/python/bin/python3` — exactly PACT's declared floor |
| Prior art | A full Python→TypeScript DSH reimplementation exists at `~/Documents/deepseek-harness/default-workspace/pact-plugin-dsh` (branch `dsh-adaptation`). It reached evidenced parity against **PACT v4.8.2**; upstream is now **v4.8.36** — 34 releases of drift, and its own logs record what re-deriving every dependency closure cost (seven failed adapter attempts, a 15-member nested closure behind one "zero missing names" module). This plan does not repeat that shape. |

---

## Architecture decision

**Contract-first. Legacy-ABI adapters. One unmodified core.**

Three layers, each with one job:

1. **PACT core** — the vendored, unmodified upstream plugin. It owns memory, journal, state, gates, handoffs, pins, variety. One implementation; nothing to drift. Its coupling surface is **extracted, not assumed**: the harness measures exactly what it depends on (Subsystem 3b), so an upstream addition becomes a build failure rather than a silent gap.
2. **Portability runtime** (`python/src/pact_harness/`) — the harness contract, the capability model, and an **ABI shim** that treats *Claude Code's hook protocol as the internal ABI*: it synthesizes Claude-shaped stdin + env, runs the vendored hook as a child process, and translates its stdout/exit code into platform-neutral directives.
3. **Platform kits** — a thin native bridge per platform (DSH cordis plugin, Claude Code wrapper, MCP server) plus a playbook compiler that emits personas, commands and skills in each platform's own format.

**Why this and not a native reimplementation**

- A native port costs a full re-derivation of ~90k lines and every implicit contract. The prior effort's own readiness log records the cost.
- An upstream bump becomes a submodule bump + conformance run instead of a re-port.
- The whole 28,619-test suite remains the regression oracle, unmodified.
- Feasibility is already proven: a real hook runs unmodified under synthesized env in 0.31–0.63 s.

**Why not "one native adapter per platform"** — N cores, N drifts, N test suites. The contract keeps adapters thin; platforms that cannot intercept get a different *tier*, not a different implementation.

**Rejected alternative, recorded**: rewriting the Python core into a platform-neutral library. It is the right long-term shape but touches 90k lines of adversarially pinned code and fails the "portable now" goal. The contract is designed so this can happen later **without changing any adapter**.

---

## Repository layout — `michael-wojcik/pact-harness`

```text
pact-harness/
├── docs/
│   ├── architecture.md
│   ├── contract.md                    # pact.event.v1 / pact.directive.v1
│   ├── adapter-authoring.md
│   └── platforms/{claude-code,dsh,generic}.md
├── vendor/pact-plugin/                # git submodule pinned to PACT v4.8.36 (commit 4607c7a)
├── python/
│   ├── pyproject.toml                 # dist: pact-harness ; console script: pact ; requires-python >=3.12
│   ├── src/pact_harness/
│   │   ├── contract/{__init__,events,directives,codec}.py
│   │   ├── contract/schema/{event.v1.json,directive.v1.json}
│   │   ├── capabilities.py
│   │   ├── runtime/{abi,env,launch,transcript,vocabulary,output}.py
│   │   ├── adapters/{base,registry,claude_code,dsh,generic_mcp,portable}.py
│   │   ├── upstream/{inspect,diff,compat}.py
│   │   ├── runtime/compat/
│   │   ├── playbook/{compile,rewrite,emit_claude,emit_dsh,emit_generic}.py
│   │   ├── install/{claude_code,dsh,generic}.py
│   │   ├── conformance/{suite,corpus}.py + conformance/fixtures/*.json + conformance/corpus/upstream/
│   │   └── cli.py
│   └── tests/
├── adapters/dsh/                      # npm bundle @pact-harness/dsh — plain ESM JS, no build step
│   ├── package.json                   # "dsh": {"bundle": {"patch": "./cordis.patch.yml"}}, engines.dsh
│   ├── cordis.patch.yml
│   ├── index.js
│   └── lib/{events,tools,commands,skills,persistence}.js
├── harness/upstream-surface.v1.json    # generated, committed, diffed on every bump
├── tools/{sync-upstream.sh,harvest.sh,conformance-dsh.sh,release.sh}
└── .github/workflows/{contract,upstream,dsh,release}.yml
```

The existing `.pact-plugin-src` clone becomes `vendor/pact-plugin` as a submodule pinned to commit `4607c7a` (tag `v4.8.36`). The vendored tree keeps its `LICENSE` untouched, and the harness ships a `NOTICE` recording the upstream MIT terms and attribution.

---

## Subsystem 1 — The harness contract

Two versioned documents, each published as JSON Schema **and** Python dataclasses.

**`pact.event.v1`** — what a platform tells PACT:

```text
EventV1 {
  v: 1, id: ULID, kind: EventKind, at: RFC3339, adapter: string,
  session: { id, cwd, project_dir?, transcript_path? },
  actor:   { role: "lead" | "teammate" | "unknown", name?, id?, type? },
  team?:   { name? },
  tool?:   { canonical, native, input, result?, error?, call_id?, parent_call_id?, is_sub_dispatch? },
  task?:   { id, subject?, team_name?, teammate_name?, status?, owner? },
  payload?: object,   # kind-specific: source, compact_summary, last_assistant_message,
                      # background_tasks, session_crons, stop_hook_active, prompt
  raw: object         # platform-native event, never logged verbatim
}
```

`EventKind` = `session.start | session.end | prompt.submit | tool.pre | tool.post | tool.fail | agent.spawn.pre | agent.start | agent.stop | agent.idle | task.completed | compact.pre | compact.post | turn.stop` — the 12 Claude Code events plus `tool.fail`, split out because Claude fires `PostToolUseFailure`.

**`pact.directive.v1`** — what PACT tells a platform:

```text
EnvelopeV1 {
  v: 1,
  directives: [Directive],
  meta: { hook, event, adapter, tier, exit, elapsed_ms, raw_stdout_bytes,
          gate_skips: [{gate, reason}] }
}

Directive =
  { kind: "deny",  reason, rule?, info? }      # veto a tool call
| { kind: "ask",   reason? }                   # route to platform approval
| { kind: "cancel" }                           # abort without a policy denial
| { kind: "allow" }
| { kind: "inject", text, target: "step" | "turn" | "user", source }
| { kind: "notify", text, level: "info" | "warning" }
| { kind: "block", reason }                    # refuse turn end
| { kind: "compact_instructions", text }       # PreCompact channel
| { kind: "silent" }
| { kind: "hook_error", hook, stage: "launch" | "exit" | "decode", message, fatal }
```

**Folding rule**: any `deny` wins and is reported once with its rule; a `hook_error:fatal` on a veto-capable event becomes `deny` with a "guard unavailable" reason; otherwise injections and notifies concatenate in registration order; `silent` is dropped when anything non-silent exists.

**Compatibility rule**: `v` is mandatory; an unknown directive kind is a decode error, never ignored.

---

## Subsystem 2 — Capability model, tiers, applicability

`capabilities.py` defines a closed enum. Every adapter declares a `CapabilitySet`; nothing is inferred.

```text
Events:      event.session.start/end · event.prompt.submit · event.tool.pre/post/fail
             event.agent.spawn.pre/start/stop/idle · event.task.completed
             event.compact.pre/post · event.turn.stop
Control:     control.tool.veto · control.tool.ask · control.tool.cancel
             control.turn.block · control.turn.steer · control.spawn.inject
Delivery:    ui.context.inject · ui.notify · ui.compact.instructions
Native gaps: native.task.board · native.messaging · native.skills · native.commands
             native.subagents.named · native.transcript · native.instruction_file
```

**Tiers (derived, never configured)**

| Tier | Requires | Meaning |
|---|---|---|
| T0 advisory | none | instructions + CLI only; PACT never sees the tool loop |
| T1 observed | tool pre/post events | PACT journals and advises; no veto |
| T2 enforced | `control.tool.veto` | pre-tool denial works |
| T3 native | T2 + named subagents + task board + compaction | full team orchestration |

**Applicability matrix** (`adapters/dsh.py::APPLICABILITY`, one row per hook module). Each row declares `required_capabilities`, `events`, and one of `run | adapt | disable | replace`. Derived from the measured matrix; representative rows:

- `bootstrap_gate`, `dispatch_gate`, `merge_guard_pre`, `worktree_guard`, `pin_caps_gate`, `pin_staleness_gate`, `git_commit_check` → require `control.tool.veto` + the canonical tool vocabulary.
- `session_init` (CLAUDE.md steps), `pin_marker_writer`, `bootstrap_prompt_gate`, `bootstrap_marker_writer`, `track_files` → `adapt`: the marker/region machinery is plain text, but the **document target** becomes the platform's instruction file.
- `roster_writer` (cloud-roster `agent_id` ↔ `tool_use_id` join) → `disable` off Claude; its premise is Claude's spawn-frame shape.
- `validate_handoff`, `stop_background_gate`, `peer_inject`, `task_claim_gate`, `file_tracker`, `teammate_idle`, `missed_wake_scan` → require named subagents / messaging; `adapt` on DSH, `disable` where absent.
- `precompact_state_reminder`, `postcompact_archive` → require compaction events; `disable` without them.

**Enforce-mode gate**: `pact doctor --require-tier T2` exits non-zero, and `pact install --mode enforce` refuses, when any `run` row's required capability is absent. Enforce mode never silently downgrades; shadow mode may degrade, and always records it.

---

## Subsystem 3 — The ABI runtime (the load-bearing piece)

`runtime/abi.py` holds the whole translation:

```python
def run_hook(hook_id: str, native: Mapping[str, Any], adapter: PlatformAdapter) -> EnvelopeV1
def run_event(event_kind: str, native: Mapping[str, Any], adapter: PlatformAdapter) -> EnvelopeV1
```

1. `adapter.normalize(native) -> EventV1`
2. For each registration of that event in `hooks.json` — **groups sequential, commands within a group parallel**, mirroring Claude Code and capping a 3-hook Bash event at ~0.7 s instead of ~1.7 s:
   a. `adapter.to_legacy(event) -> LegacyInvocation(script, stdin, env, timeout_ms, async_)`
   b. `runtime/launch.py` executes `python3 <vendored hook>` with that stdin/env, honoring the registration's `timeout` (600 s for `pin_caps_gate`) and `async` flag (fire-and-forget for `pin_marker_writer`, `session_end`, `file_tracker`, `roster_writer`)
   c. `adapter.from_legacy(result, event) -> [Directive]`
3. Fold and return.

**Legacy-output decoding** (the complete set of shapes the 28 modules can emit):

| Observed | Directive |
|---|---|
| exit 2, stderr only | `deny(reason = stderr)` — and the shim also writes the reason to stderr for hosts that read either channel. Upstream contains a documented contradiction about which channel survives exit 2; emitting both is the only safe reading. |
| exit 2 + `hookSpecificOutput.permissionDecision = "deny"` | `deny(reason)` |
| `permissionDecision = "ask"` / `"defer"` | `ask(reason)` / `allow` + `inject` ("defer" means let the host decide) |
| `hookSpecificOutput.additionalContext` | `inject(target = event-appropriate)` |
| `systemMessage` | `notify(level = warning)` |
| `{"decision": "block", "reason": ...}` (Stop / SubagentStop) | `block(reason)` |
| `{"custom_instructions": ...}` (PreCompact) | `compact_instructions(text)` |
| `{"suppressOutput": true}` / `{}` / empty | `silent` |
| exit 1 / crash / timeout | `hook_error(stage = "exit")` → **allow**. The hooks already own their fail direction and the fail-closed ones emit `deny` themselves; the shim must never invent one. |
| Python missing / stdout undecodable | `hook_error(fatal = true)` → `deny` on veto events, plus an activation record and a non-zero `doctor`. |

**Env contract** (`runtime/env.py`), derived from upstream's 44 direct env reads:

| Var | Value | Why |
|---|---|---|
| `CLAUDE_CONFIG_DIR` | resolved PACT state root | the one true resolver (`shared/paths.py`) |
| `CLAUDE_PROJECT_DIR` | event `session.project_dir` | 13 reads, no resolver — this is the load-bearing synthesis |
| `CLAUDE_PLUGIN_ROOT` | vendored plugin root | symlink setup, protocol references |
| `CLAUDE_CODE_SESSION_ID` | event session id | project-scope records |
| `CLAUDE_PID` | harness pid | process-key files |
| `CLAUDE_ENV_FILE` | unset by default | Claude-only Bash-env channel; documented degradation |
| `PACT_*` | passthrough | PACT's own config registry |
| `PACT_ADAPTER`, `PACT_HOME` | harness-only | never read by upstream |

**Interpreter policy**: `PACT_PYTHON` → `python3` when it is ≥3.12 → DSH's bundled 3.12 when `DSH_HOME` is present. Refuse <3.12; warn on >3.12, because upstream CI certifies only 3.12. `doctor` detects a PEP 668 externally-managed interpreter and an app-bundle interpreter and says so; `pact install --venv` builds `$PACT_HOME/venv` on 3.12 for the memory CLI. The hooks are stdlib-only, so the venv is never required for them.

**State-root policy**: `PACT_HOME` → (`claude-code` adapter only) `CLAUDE_CONFIG_DIR` → `~/.claude` for the Claude adapter, `~/.pact` for every other adapter. `pact migrate-state --to DIR
pact memory status --json                      # backend identity, location, schema, model, counts
pact persona show --platform ID                # exactly what the platform receives
pact mode [--gate ID=shadow|enforce|off] [--adapter ID]` relocates; `PACT_HOME=~/.claude` shares one state across platforms. **Zero upstream edits** — the shim sets `CLAUDE_CONFIG_DIR`, which `paths.py` already honors. Documented quirk: `get_backlog_dir()` is deliberately home-pinned to `~/.claude/pact-backlog`.

**Memory access.** The harness never opens `memory.db`, never computes a store path, and keeps no cache. It calls a `MemoryBackend` protocol (save, search, get, list, update, delete) whose only v1 implementation shells out to the `pact-memory` CLI — honouring that skill's own rule that the CLI, not the caller, resolves the store. `pact memory status --json` reports backend identity, location, schema version, embedding model and dimension, and counts. This is the seam a future external or synced memory layer replaces, without changes to adapters, playbooks or the state-root policy.

**Tool vocabulary** (`runtime/vocabulary.py`): a canonical ↔ native bidirectional map per adapter, covering at minimum `Bash`, `Read`, `Write`, `Edit`, `Glob`, `Grep`, `Agent`, `Task{Create,Update,List,Get}`, `SendMessage`, `Skill`, `AskUserQuestion`, `TodoWrite`, plus every mutator the host ships. **Completeness is enforced, not assumed**: `pact doctor` enumerates the adapter's declared tool catalog and fails when a mutating tool has no mapping — an unmapped mutator would silently bypass `bootstrap_gate`'s blocked set.

**Argument normalization**: file tools must always yield `tool_input.file_path`. Hosts disagree (`edit`/`write` use `file_path`; `str_replace_editor` uses `path`; `apply_patch` is multi-file). When a mutator cannot yield one path, the adapter leaves `file_path` absent and path-dependent gates record `gate_skipped_no_path` in the envelope meta rather than silently passing; `doctor` surfaces the list.

**Transcript synthesis** (`runtime/transcript.py`): seven modules read `transcript_path`. The adapter supplies either a host transcript or a synthesized JSONL carrying only the fields PACT reads (`entrypoint`, last assistant message). Hooks needing more degrade exactly as they do today on a missing file.

---

## Subsystem 3b — Upstream compatibility (how the harness absorbs your PACT-Plugin changes)

**Premise.** PACT-Plugin remains a live Claude Code plugin that you keep changing. Every upstream commit is a potential harness input, so the harness must absorb it by **measurement**, never by re-reading 90k lines and never by forking. This subsystem is the answer, and it is also the reason the rest of the plan can stay thin.

**A. The upstream surface manifest.** `pact upstream inspect --plugin DIR` mechanically extracts `harness/upstream-surface.v1.json`:

- every registration — module, event, matcher, timeout, `async`;
- every decision shape — AST-extracted `permissionDecision` values, exit-2 sites, `decision:"block"`, `custom_instructions`, `additionalContext`, `systemMessage`, `suppressOutput`;
- every stdin field read and every env var read, each with `file:line` and whether a central resolver exists;
- the tool vocabulary — AST-extracted `_BLOCKED_TOOLS`, `_READ_ONLY_TOOLS`, `_DEGRADED_ASK_TOOLS`, plus every literal tool name appearing in a matcher;
- the artifact inventory — `agents/`, `commands/`, `skills/`, `protocols/`, `reference/` with sha256 and parsed frontmatter;
- the CLI entry points that commands shell out to (`python3 -I …` call sites), which are exactly what the compiler must replace.

Nothing in this manifest is hand-maintained. If upstream adds a hook, a tool name, an env var, a command, a skill or a protocol, it appears here on the next refresh. Vocabulary requirements, the env contract, applicability rows and playbook inputs are **derived from the manifest** — so upstream additions become coverage gaps the build fails on rather than silent under-enforcement.

**B. Five compatibility levels, classified by diffing two manifests.**

| Level | Delta | Harness action | Automation |
|---|---|---|---|
| L0 | no surface change | none | drop-in |
| L1 | artifact-only (skills / commands / protocols / prose) | recompile playbook | fully automatic |
| L2 | additive ABI (new hook, tool name, stdin field, env var, decision shape) | new hooks run automatically; extend vocabulary / env / applicability | semi-automatic: build fails until the mapping is added |
| L3 | breaking ABI (renamed or removed field, changed output shape) | version-keyed shim in `runtime/compat/` | manual, bounded |
| L4 | structural (hook protocol replaced) | adapter and runtime release | manual, planned |

`pact upstream diff OLD NEW --json` prints the classification with a per-item rationale. `pact upstream verify` fails on any L2+ delta not covered by a mapping, and on any manifest element the harness did not account for.

**C. The differential corpus grows with upstream's own tests.** Upstream's 28,619 tests call hooks in-process, so they can be recorded without modification. `tools/harvest.sh` runs the upstream suite with a recorder shim and emits `(stdin → exit, stdout, stderr)` pairs into `conformance/corpus/upstream/<version>/`; the ABI parity lane replays them. This is what makes "byte-identical" durable rather than a snapshot: new upstream tests become new conformance cases automatically, and an upstream behavior change surfaces as a corpus diff.

**D. The sync pipeline.** `tools/sync-upstream.sh` plus `.github/workflows/upstream.yml`:

1. fetch and detect a new tag or commit;
2. bump the submodule;
3. regenerate the surface manifest and harvest the corpus;
4. classify the delta and write an impact report;
5. run contract + ABI parity + adapter conformance;
6. open a PR carrying the bump, the report and any mapping changes — or fail with the report attached.

Lanes: **nightly against upstream `main`** (drift is caught before a release exists), **per harness PR**, and **on tag**.

**Release cadence.** The harness follows every upstream release: upstream tags, the sync pipeline absorbs it, conformance proves it, and the harness publishes. A red conformance run **blocks** the harness release, so users stay on the previous harness release with its working PACT — an upstream break can never reach them through a lazy sync. Most upstream releases are artifact-only and therefore a reviewed auto-PR plus a patch release, not development.

**E. Your inner loop.** `PACT_PLUGIN_ROOT=/path/to/PACT-Plugin pact …` (also `pact dev link --plugin DIR`) runs the harness against a working checkout instead of the pinned copy. `pact doctor` prints which tree is in use, its version, and whether it is dirty. `pact upstream verify --plugin DIR` answers "does my in-progress upstream change break the harness?" in one command. An optional thin reusable workflow (`.github/workflows/harness-compat.yml`) can be dropped into PACT-Plugin to run that check on upstream PRs — it changes nothing about the plugin's Claude Code behavior.

**F. Failure direction.** A coverage gap always fails **closed**: an unmapped mutating tool, an unaccounted env read, a hook module with no applicability row, or an unknown decision shape blocks the build and `doctor`. The harness never silently runs with less enforcement than it claims.

---

## Subsystem 4 — Adapter SDK

`adapters/base.py` — one Protocol, five methods, a capability declaration and an applicability table:

```python
class PlatformAdapter(Protocol):
    id: str
    capabilities: CapabilitySet
    applicability: Mapping[str, Applies]
    def normalize(self, native: Mapping[str, Any]) -> EventV1: ...
    def to_legacy(self, event: EventV1) -> LegacyInvocation: ...
    def from_legacy(self, result: LegacyResult, event: EventV1) -> list[Directive]: ...
    def render(self, env: EnvelopeV1, event: EventV1) -> NativeResponse: ...
```

`adapters/registry.py` resolves `--adapter` → `PACT_ADAPTER` → auto-detect (`CLAUDE_PLUGIN_ROOT` + Claude frame shape → `claude-code`; `DSH_SESSION_ID`/`DSH_PROFILE` → `dsh`; else `generic-mcp`), and refuses ambiguous detection rather than guessing.

---

## Subsystem 5 — Platform adapters

### 5a. Claude Code (`adapters/claude_code.py`) — the zero-regression path

Two install modes:

- **`native` (default)**: upstream `hooks.json` is used untouched. The harness is present for `doctor`, playbook builds and conformance, but intercepts nothing. Zero risk; existing users are unaffected on upgrade.
- **`bridged` (opt-in)**: `hooks.json` is regenerated so every registration becomes `python3 -m pact_harness hook <hook-id> --adapter claude-code`. This enables a single code path, platform attribution and cross-platform vocabulary. It ships only after M1 proves byte-identical decisions on the captured corpus.

The Claude adapter is a near-identity mapping: `normalize` reads the real frame fields, `to_legacy` reproduces the original stdin exactly, `from_legacy` reproduces the original stdout/exit under `--raw`.

### 5b. DeepSeek Harness (`adapters/dsh/`) — the centerpiece

A cordis bundle: `export function apply(ctx, config)`, `package.json` with `"dsh": {"bundle": {"patch": "./cordis.patch.yml"}}` and an `engines.dsh` range, plain ESM JS (no build step, no runtime deps beyond host peers).

**Event mapping (verified against 0.2.2-rc.5 sources):**

| PACT event | DSH extension point | Mode | Directive rendering |
|---|---|---|---|
| `session.start` | `agent/created` `{agent, source, signal?}` | serial, awaited | `agent.inject()` |
| `session.end` | `session/disposed` + `session/flush` durability barrier | emit / parallel | observe only |
| `prompt.submit` | `agent/pre-step` `{agent, messages, turn, step, signal, modelSelection}` | waterfall | `{kind:'enter', messages}` to inject; `{kind:'reject'}` to refuse |
| `tool.pre` | `tools/pre-execute` `(exec, next)` | waterfall | `allow` / `deny{reason}` / `ask{reason, displayReason}` / `cancel` |
| `tool.post` / `tool.fail` | `tools/post-execute` `(exec, result, next)` | waterfall | `accept{additionalContexts}` / `block{feedback}` |
| `agent.start` / `agent.stop` | `subagent/start` / `subagent/end` | emit, tree-scoped | observe + inject into child |
| `task.completed` | `session/event` on `team/task` with `status:'completed'` | emit | journal |
| `compact.pre` / `compact.post` | `session/event` compaction events | emit | observe / archive |
| `turn.stop` | `agent/turn-stopping` | serial (steers, does not veto) | `block` → `agent.steer(createUserMessage(...))` |
| `agent.idle` | `agent/status` `{status:'idle'}` | emit | advise |

**Capabilities: T3.** Every PACT event has a home and every directive has a native rendering.

**Mode policy.** A fresh install enforces exactly two gates — `merge_guard_pre` and `git_commit_check` — because both read `tool_input.command` and DSH's `bash` supplies that key unchanged, so no mapping sits between the gate and its input. The other seven refusing gates stay in shadow until the M2 conformance run proves their name and argument mapping. `pact mode` edits the per-gate table; `doctor` prints it.

**Tools the bridge supplies** (`lib/tools.js`):
- `pact_agent` — **PACT-shaped delegation** taking `{subagent_type, name, prompt, description}`, resolving the specialist to its compiled persona and calling `ctx.subagents.startActivation(...)`. DSH's own `subagent` tool has no type parameter, so this compatibility tool is what makes PACT's prose work unchanged. Registered only when `ctx.subagents` is present.
- `pact_task_{create,update,list,get}` **fallback tools** over the harness task store, registered only when `ctx.agentTeams` is absent (DSH's team tools exist only when the team plugin is mounted, and it defaults to `defaultEnabled: false`). When `ctx.agentTeams` is present, the bridge maps `team_task_*` ↔ canonical `Task*` instead and registers nothing.
- The bridge never shadows a native tool name.

**Commands** (`lib/commands.js`): `ctx.commands.register({name: 'pact-orchestrate', input: {hint}, handler})` for the 13 PACT commands (all but `telegram-setup`), names matching DSH's `/^[a-z][a-z0-9_-]*$/`. The handler injects the compiled command body with `{arguments}` substituted via `invocation.agent.followup(createUserMessage({content, source: {kind: 'pact-harness'}}))` — required because a DSH command handler returns UI text and does not talk to the model itself.

**Personas** (`lib/personas.js`): the orchestrator persona is delivered as a `ctx.systemPrompt.section({name: 'pact:orchestrator', order, text})` — the closest analogue of Claude's `--agent` and stronger than a file, because it cannot be accidentally edited. Specialist personas are injected into children at `agent/created` via `agent.inject()`. A managed `AGENTS.md` section is the fallback only when the prompt-section API is unavailable.

**Skills**: a zero-copy `ctx.skills.registerProvider({name: 'pact', list, get})` over the vendored `skills/` tree. DSH accepts `name` + `description` frontmatter and requires kebab-case names — PACT's already conform. The installer also offers `--copy-skills` into `<project>/.dsh/skills` or `$DSH_HOME/skills`.

**Sub-dispatch safety**: DSH's `run_code` can sub-dispatch tools. `tools/pre-execute` sees those calls (`parent` / `rootCallId`), so the bridge gates them too and marks `is_sub_dispatch`. A conformance test proves a mutating sub-call is denied exactly as a direct call is.

**Install** (`install/dsh.py`): writes a profile (default `pact`) from the shipped headless template plus model and permission rows, adds the bundle to `dsh.profile.bundles`, and links `adapters/dsh` into it. Fully reversible with `pact uninstall dsh`. The harness never touches the `desktop` profile — it is Electron-managed and cannot be CLI-booted.

### 5c. Generic / MCP (`adapters/generic_mcp.py`) — the long tail

`pact mcp serve` speaks MCP over stdio:
- tools: `pact_event`, `pact_gate`, `pact_memory_{save,search,get,list}`, `pact_task_{create,update,list,get}`, `pact_journal_{append,read}`, `pact_handoff`, `pact_pin_{add,prune}`, `pact_state`
- resources: `pact://session/state`, `pact://journal`, `pact://memory`

Plus `pact install generic`, which writes a managed PACT section into `AGENTS.md` and skills into `~/.agents/skills` (a root both DSH and other AGENTS.md tools read).

**Declared tier: T1.** It has no veto, and `pact doctor` says so in its first line. Enforcement claims are refused.

---

## Subsystem 6 — Playbook compiler

Source of truth = vendored `agents/`, `commands/`, `skills/`, `protocols/`, `reference/`. One compiler, per-platform emitters. Rewrites required (all measured):

| Source form | Problem off-Claude | Rewrite |
|---|---|---|
| `$ARGUMENTS` + `argument-hint` (6 commands) | Claude-only substitution | `{arguments}` template consumed by the platform command handler |
| `Read(file_path="../protocols/pact-X.md")` (orchestrator, 14 refs) and `[pact-X.md](../protocols/...)` (specialists) | relative paths break outside the plugin tree | resolve to `resourceBase`-relative paths, or inline the protocol at compile time when the platform has no file-read primitive |
| `python3 -I <plugin>/hooks/shared/...py` (**50 refs** across commands) | commands drive state through shell | rewrite to `pact ...` CLI verbs shipped by the harness |
| `ToolSearch "select:TaskCreate,..."` (orchestrator) | Claude-only deferred-tool loading | delete; the platform exposes the tools directly, or the harness registers fallbacks |
| agent frontmatter `model`, `permissionMode`, `memory`, `skills` (preload) | Claude-only fields | map where a platform equivalent exists; drop with a recorded note otherwise |
| `user_invokable` in two SKILL.md files | non-standard | emit `user-invocable` |
| `Agent(name=, team_name=, subagent_type=, ...)` prose | DSH's `subagent` has no type | keep the prose; the bridge's `pact_agent` accepts exactly that shape |
| `pact-protocols.md` (187 KB index) | prompt budget | never auto-inlined; always a lazily-read resource |

Outputs: `claude-code` (pass-through — upstream artifacts are already correct), `dsh` (prompt sections, commands, skill-provider metadata), `generic` (`AGENTS.md` section + skill files + MCP instructions). The compiler is deterministic and golden-tested.

---

## Subsystem 7 — CLI, install, doctor

```text
pact version                                   # harness version + pinned PACT version + adapter
pact doctor [--adapter ID] [--json] [--require-tier T2]
pact capabilities [--adapter ID]
pact install <claude-code|dsh|generic> [--mode native|bridged] [--profile NAME] [--state-root DIR] [--venv]
pact uninstall <platform>
pact paths                                     # resolved state root, project dir, plugin root
pact migrate-state --to DIR
pact hook <hook-id> --adapter ID [--raw]        # single hook, JSON in/out
pact event <kind> --adapter ID                  # all hooks for one event
pact playbook build --platform ID --out DIR
pact conformance [--adapter ID] [--corpus DIR]
pact upstream inspect --plugin DIR --out FILE   # extract the surface manifest
pact upstream diff OLD NEW [--json]              # classify L0-L4
pact upstream verify [--plugin DIR]              # does this tree still fit the harness?
pact upstream check                             # how far behind the latest upstream tag?
pact corpus harvest [--out DIR]                  # record upstream's suite into the parity corpus
pact dev link --plugin DIR | pact dev unlink     # run against a working checkout
pact mcp serve
```

A bare `pact`, or one with flags only (`pact --resume`), passes through to `claude --agent PACT:pact-orchestrator` exactly as upstream's launcher does today; an unrecognised subcommand prints a suggestion rather than forwarding silently.

`pact doctor` is the honesty surface: tier, each capability with its source, each hook module with `run | adapt | disable | replace` and the reason, unmapped mutating tools, Python version against the PACT floor (3.12; 3.13/3.14 marked unverified because upstream CI certifies 3.12 only), and the last activation record. It exits non-zero whenever a claim cannot be backed.

---

## Subsystem 8 — Conformance suite

Four layers, each with a pass/fail oracle:

1. **Contract** — schema validation and round-trip for every fixture; an unknown directive kind fails.
2. **ABI parity (Claude)** — for all 34 registrations: (a) `pact hook --raw` reproduces the direct hook's stdout/exit byte-for-byte on the captured corpus (`tests/fixtures/role_frames.py` plus a synthetic matrix); (b) semantic directives equal a written expectation table. This is the gate for bridged mode.
3. **Adapter conformance** — per adapter: every declared capability is exercised; every applicability row behaves as declared; a mutating tool cannot bypass a mapped gate; a sub-dispatch cannot bypass it; an unmapped mutator is reported.
4. **End-to-end** — DSH headless boot, Claude parity suite, generic MCP scripted client. Split by cost: every PR runs the no-model half (plugin activation, journal write, skill catalog, command registration, and a deny driven directly through the extension point); a nightly job with `DEEPSEEK_API_KEY` runs the full model turn. Flaky model availability therefore never blocks a merge.

**DSH E2E recipe** (verified invocation):

```bash
NODE="/Applications/DeepSeek Harness.app/Contents/Resources/runtime/bin/node"
DSH="/Applications/DeepSeek Harness.app/Contents/Resources/app.asar/dsh/node_modules/@deepseek-ai/dsh/lib/bin.js"
"$NODE" "$DSH" --profile pact --dump-config        # composition check; imports are not mounted
"$NODE" "$DSH" --profile pact --json "Reply with exactly: PACT-OK"
```

Assertions: exit 0; no `did not activate` on stderr; `final.reason.kind === "completed"`; activation record written; a journal event appended under the state root; `pact-memory` present in the skill catalog; `/pact-orchestrate` registered; and a **proven deny** — a scripted action in a blocked state is refused with PACT's reason. Headless needs `DEEPSEEK_API_KEY`; the boot-time half of the suite runs without a model by asserting the activation record and the deny path.

---

## Edge cases and failure modes (each with an explicit rule)

| Case | Rule |
|---|---|
| Hook crash / timeout | `hook_error(stage="exit")` → allow. Hooks own their fail direction; the shim never invents one. |
| Python missing / output undecodable | `hook_error(fatal=true)` → deny on veto events, notify elsewhere, always recorded, `doctor` non-zero. |
| Missing capability in enforce mode | Refuse install/doctor; never silently downgrade. |
| Unmapped mutating tool | `doctor` fails; gate-skipped diagnostics recorded on the envelope. |
| Path-dependent gate with no `file_path` (`apply_patch`) | `gate_skipped_no_path` in meta, surfaced by doctor; never a silent pass. |
| Sub-dispatch via `run_code` | Gated identically; marked `is_sub_dispatch`. |
| Multi-file mutation | Treated as write-class; path gates skip with a diagnostic. |
| Upstream deny/exit-2 channel ambiguity | Emit both JSON and the stderr reason. |
| Async hooks | Fire-and-forget; failures logged, never block. |
| Several hooks on one event | Groups sequential, commands within a group parallel; deny wins. |
| Two platforms sharing a state root | Upstream locking applies; doctor warns on unsupported cross-version sharing. |
| Python 3.13 / 3.14 | Runs, but doctor marks it unverified (upstream certifies 3.12 only). |
| DSH API drift (rc build) | `engines.dsh` range + conformance suite + a bridge kept thin (about 600–900 lines) enough to repair in a day. |
| `desktop` profile | Never touched; Electron-managed and not CLI-bootable. |
| Upstream adds a hook registration | it appears in the registry automatically and runs; if it needs an unmapped tool, env var or applicability row, the build fails naming that exact item |
| Upstream renames or deletes a hook module | an applicability row references a missing module — build fails; the row is updated or retired |
| Upstream adds a tool name to a gate's blocked set | the vocabulary requirement grows from the AST extract; `doctor` fails until the platform maps it |
| Upstream adds a new stdin field | adapters omit it by default and it is recorded as adapter-supplied; hooks must already degrade on absence |
| Upstream changes an output shape | the decode table test fails with the captured raw sample attached |
| Upstream changes prose only | L1: playbook recompile, zero code change |
| Running against an un-pinned or dirty checkout | `doctor` marks the tree, its version and dirty state; conformance still runs |
| Windows | Out of scope for M1–M3; the adapter interface is OS-neutral, and the warm-worker optimization below is POSIX-gated. |

---

## Phased delivery

| Milestone | Deliverable | Exit criteria |
|---|---|---|
| **M0 — skeleton** (1–2 d) | repo, submodule pin, contract v1 + schemas, CLI skeleton, `doctor` / `paths` / `version` | schema round-trip tests green; `pact version` prints both versions |
| **M1 — ABI + Claude parity** (3–5 d) | `runtime/`, `adapters/{base,registry,claude_code}`, conformance corpus from captured frames | 34/34 registrations raw-byte-identical; upstream pytest suite green unmodified against the recorded baseline; bridged mode installs and uninstalls cleanly |
| **M2 — DSH enforcement** (5–8 d) | `adapters/dsh` bridge (events, tool mapping, decisions, persona injection), `install/dsh`, E2E harness | DSH headless E2E passes every assertion including a proven deny; no `did not activate`; exactly `merge_guard_pre` and `git_commit_check` enforce, the rest shadow |
| **M3 — playbook** (3–5 d) | compiler + DSH emitters (commands, skills provider, personas) | `/pact-orchestrate` runs from DSH; all 21 skills catalogued; golden output tests |
| **M4 — generic** (3–5 d) | MCP server, `AGENTS.md` / skills installer, `pact mcp serve` | a scripted MCP client writes memory + journal; doctor reports T1 and refuses enforce |
| **M5 — upstream sync** (3–4 d) | surface inspector, manifest diff and classification, corpus harvester, `upstream.yml` nightly lane, `pact dev link` | a deliberately breaking change made in a scratch checkout is caught by `pact upstream verify` with a correct L2/L3 classification; an artifact-only change is absorbed with zero code edits |
| **M6 — release** (2–3 d) | docs, install UX, release notes | a fresh machine can follow the DSH quickstart to a running PACT session; upstream and model nightly lanes are green; the first harness release follows an upstream release end to end |

Indicative total: **4–5 weeks** of focused work, with M2 as the critical path and the upstream sync lane (M5) as the steady-state operating cost thereafter. If per-hook latency proves unpleasant in real DSH sessions, a Phase-E optimization pre-warms the interpreter in a daemon and forks per hook (POSIX only); it is additive and changes no contract.

---

## Public API and schema changes

**New (this repo)** — `pact.event.v1`, `pact.directive.v1`, the generated `upstream-surface.v1` manifest and its L0–L4 compatibility vocabulary, the `pact` CLI, `PACT_ADAPTER` / `PACT_HOME` / `PACT_PLUGIN_ROOT`, adapter manifests, the npm bundle `@pact-harness/dsh`, the PyPI distribution `pact-harness`.

**Changed in upstream** — **none.** No file under `vendor/pact-plugin` is modified, enforced by a CI check that diffs the submodule against its pinned tag.

---

## Assumptions (say so now if any is wrong)

1. **New repo, upstream as a live pinned dependency.** The harness lives in `michael-wojcik/pact-harness`; `PACT-Plugin` stays an unmodified, actively developed Claude Code plugin and remains the source of truth for the core. The harness never forks or patches it. Upstream change is absorbed by the pipeline in Subsystem 3b, not by editing the plugin.
2. **Python 3.12+ at runtime on every platform.** Not a new dependency: PACT already requires it. DSH ships 3.12.14; `PACT_PYTHON` overrides.
3. **Priority order** — Claude Code (no regression) → DSH (full) → generic MCP (advisory). Specific adapters for Cursor / Codex / Gemini are **not** built in this plan; they are covered by the generic adapter and by the adapter SDK if demand appears.
4. **The prior DSH port is prior art, not merged.** Its tool-vocabulary table, extension-point mapping and recorded DSH gotchas are reused as evidence; its TypeScript core is not.
5. **State defaults** — `~/.claude` for the Claude adapter (unchanged), `~/.pact` for others; `PACT_HOME=~/.claude` shares one state across platforms.
6. **"Any platform" means any platform with either hooks or MCP.** Platforms with neither get T0 (instructions + CLI only), stated rather than hidden.
7. **Upstream churn is the steady state, not an exception.** The posture is a pinned commit plus a nightly check against upstream `main`, with a one-command check against any working checkout via `PACT_PLUGIN_ROOT`.
8. **Memory is a backend, not a directory.** The harness never reads `memory.db`; it calls a `MemoryBackend` whose v1 implementation shells to the `pact-memory` CLI, and reports through `pact memory status --json`. A future external or synced store replaces the backend without touching adapters, playbooks or the state-root policy.

---

## Non-goals

- Reimplementing the PACT core in another language.
- Modifying upstream PACT files.
- A publishing/marketplace pipeline for the DSH bundle beyond a link-installable npm bundle.
- Windows parity.
- Replacing the Claude Code plugin for existing users (bridged mode is opt-in).
