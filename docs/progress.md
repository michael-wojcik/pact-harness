# Progress

Updated as milestones land. This file is the handover point: a fresh session
reads this and [plan.md](plan.md) before touching code.

## Where we are

- **M0 — complete.** Contract v1, capability model, vendor pin, CLI, doctor, CI.
- **M1 — complete.** Bridged Claude Code mode is implemented and gated: the
  installer re-runs byte parity (48 cells, stdout and exit code) and refuses to
  install unless it holds, then copies the tree and rewrites all 34 commands to
  call the shim; uninstall refuses a directory it did not make. The planned CLI surface is closed: `pact hook`
  runs one registration against one frame with `--raw`, `pact migrate-state`
  moves a state root and refuses a partial move, and `pact install claude-code`
  reports native mode while refusing bridged mode with its reason. `doctor` now
  prints the hook-module inventory (28 modules, the 3 async ones), an
  applicability table derived from each adapter's capability set (claude-code and
  dsh: 28 run; mcp: 28 disable, each naming the event capability it lacks), and
  the last activation record. `tools/run-upstream-tests.sh`
  runs upstream's own suite unmodified against a local clone of the submodule (a
  plain copy fails tests that shell out to git) and the contract lane runs it on
  every pull request. Measured here: 21 of ~28,000 fail on macOS, 19 after
  installing upstream's pinned pytest, and all 19 are platform assumptions --
  invalid-UTF-8 filenames APFS refuses, /bin/true, and CI-only seams -- recorded
  with reasons in `tools/upstream-known-macos-gaps.txt`; with those deselected
  the affected files pass 627/627. The contract lane runs the suite on Ubuntu
  and Python 3.12, upstream's certified platform, with no deselects. See
  [docs/upstream-suite.md](upstream-suite.md). Registry, decoder, child environment, launcher, transport
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
- **M3 — complete.** The playbook compiler extracts 13 personas, 13 commands
  (Telegram excluded), 21 skills, 22 protocols and 3 references, resolves every
  plugin-relative reference to an absolute path, and replaces Claude's
  `$ARGUMENTS` with the platform placeholder. The installer compiles it next to
  the profile and the bridge registers the orchestrator persona as a system
  prompt section at order 0, registers the 13 `/pact-*` commands (handlers
  inject the compiled body with the user's text substituted), registers the 21
  skills as a zero-copy provider, and gives a teammate agent its specialist
  persona on creation. The `pact_agent` tool closes the last gap: DSH's delegation tool has no type
  parameter, so the tool accepts PACT's `subagent_type` shape and starts an
  activation whose label is the persona without its prefix. Its definition was
  compiled by the real `defineTool` (required: subagent_type and prompt) and its
  execute path exercised against the real service shape.

- **M4 — complete.** `pact install generic` writes the managed AGENTS.md
  section and symlinks the 21 skills into the shared skills root, with uninstall
  removing only the links it can prove it made. `pact dev link` records a working
  checkout for the whole harness: precedence is PACT_PLUGIN_ROOT, then the link,
  then the pin, a deleted tree degrades to the pin, and `doctor` reports the
  override and whether the tree is dirty. The M5 exit criterion is proven against
  scratch copies of the real tree: an artifact-only edit is L1 and the playbook
  still compiles with no code change, a new tool name and a new environment read
  are L2 and verify names each one, a removed registration is L3, and an emptied
  registry is L4. The generic MCP adapter serves newline-delimited
  JSON-RPC on stdio: initialize, ping, tools/list and tools/call for
  `pact_event`, `pact_gate` and `pact_status`. It is the honest T0 — the tools
  say "advisory only" in their text, and `pact_status` reports tier T0 and the
  live registry. The memory backend seam is in: location resolved through the
  skill's own config module in an isolated interpreter, presence reported by
  stat rather than by opening the store, and save/search routed through the
  vendor CLI. The journal tools give a foreign agent a namespace of its own under the harness
  state root; writing into a PACT session directory would make harness events
  indistinguishable from a real session's history.
- **M5 — complete.** `pact corpus harvest` records every registration's verdict
  (exit code, directive kinds, decode health) for all 24 frames; `replay`
  compares a tree against a baseline and names each changed, added or removed
  cell. The baseline for the pin is committed at
  `harness/corpus/pact-4.8.36.json` (48 cells, 28 hooks) and a test fails if the
  tree stops reproducing it. `sync-upstream.sh` replays it after a bump, because
  conformance asks whether every registration is understood while replay asks
  whether any of them now answers differently. `pact upstream inspect` extracts the surface: 34
  registrations, the matcher vocabulary, the three gate tool-sets, 14
  environment reads with their sites (including reads that go through a module
  constant), 73 hashed artifacts and the decision-token counts. A baseline is
  committed at `harness/upstream-surface.v1.json` and a test fails if the pin
  moves without regenerating it. `pact upstream verify` classifies each read and
  each tool name against the harness's contract and names the gaps. A nightly
  lane runs verify against upstream `main`. `pact upstream diff OLD NEW`
  classifies a change L0-L4 and is keyed by (event, hook, matcher) rather than
  manifest position, so a reordering is not a wave of removals; matcher strings
  and matcher tokens are both compared, because grouping changes stop hooks
  running on a tool while every token survives. `pact upstream check` reads the
  repository's tags and reports how far the pin is from the latest release
  (verified against the network: pinned 4.8.36, latest 4.8.36, 0 behind).
  `tools/sync-upstream.sh` performs the whole absorb: fetch, checkout,
  regenerate, classify, verify, conformance, rewrite the pin — and never
  commits, so a human reviews what moved. A guard test compiles the script's
  embedded Python, because a mangled heredoc escape is a real defect this
  session hit.
- **M6 — complete except publication.** `pact install --venv` creates the memory
  CLI's environment under the state home (PEP 668 is why it exists), and
  `tools/harness-compat.yml` is a template upstream can drop into its own CI to
  run `pact upstream verify` on pull requests. Release docs landed: a rewritten README with an install
  path per platform, [docs/architecture.md](architecture.md), per-platform
  quickstarts, a changelog, a version-identity test that holds pyproject,
  package and schemas to one number, and a tag-triggered release lane that
  refuses a tag which disagrees with any manifest.
  Publishing the first release needs a tagged remote, which this environment
  does not have; the lane that would run on that tag is committed and checked.
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

## Defects found by measuring, not by reading

| Symptom | Cause | Fix |
|---|---|---|
| Decisions silently missing from a forked hook | `os._exit` skips interpreter shutdown, discarding buffered stdout | flush before exit |
| Decisions written into the embedder's buffer | the child inherited the parent's `sys.stdout` object | rebind the child's stdio to its descriptors |
| A caller timeout with no error | a pipe's EOF waits on every process holding the write end | temp file plus polled `waitpid` |
| A boot with the bridge active died as `message "undefined" is already pending` | the fallback context message had no `id`, and DSH's inbox requires unique ids for every pending message; the host's `createUserMessage` import (which assigns one) failed from the linked plugin directory | prefer the host helper, and give the fallback a unique id; verified by a real boot reaching MISSING_CREDENTIAL and then AUTH |
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

## Repository and release

The project is public at https://github.com/michael-wojcik/pact-harness. The MIT
license is in the repository, released as v0.1.2 with both artifacts.

History was rewritten to remove the one local machine path that had been
committed inside the surface manifest: branches and tags now carry no such
string, and the tree is byte-identical to the pre-rewrite tree (same tree hash).
Commit hashes changed and v0.1.1's tag/release were removed; v0.1.2 supersedes
it. GitHub can still serve the pre-rewrite commits by exact SHA until its
housekeeping collects them, so a guaranteed purge would mean recreating the
repository.

Every lane is green: contract (including upstream's unmodified suite), release,
and model-nightly. A live model-backed PACT turn has been proven both in CI and
locally.

## Next

Everything in plan.md is implemented and published. The project is public, and
all four lanes are green.

Releases now publish to three places from one tag: GitHub release, PyPI
(trusted publishing) and npm (trusted publishing). No registry token is stored;
both use OIDC. v0.1.4 is the first release on every channel.

Two operational notes:

- A brand-new npm package serves its full metadata immediately but its
  abbreviated install metadata can lag by about a quarter hour, so pnpm-based
  installs may report 404 for the first few minutes after publish.
- Rotate or remove the DEEPSEEK_API_KEY repository secret if the nightly model
  lane is ever retired.
