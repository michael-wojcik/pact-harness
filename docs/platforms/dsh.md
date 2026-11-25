# PACT on DeepSeek Harness

The bridge is a cordis plugin. It maps DSH's extension points onto
`pact.event.v1`, evaluates through a warm worker, and renders the answer back
into DSH's own decision types. All of PACT's policy stays in the vendored
Python; the bridge contains no gate logic.

## Install

Requires Python 3.12+ and the `dsh` CLI.

```sh
pact install dsh --profile pact
```

What that does, in order:

1. Materialises a bootable profile from the shipped `headless` template.
   Registering a bundle alone composes but cannot boot, so the template comes
   first.
2. Links the bridge into the profile with `dsh plugin add`.
3. Compiles PACT's artifacts next to the profile (`pact-playbook.json`).
4. Writes a delimited managed block into the profile's patch layer with the
   enforcement policy and the playbook path.

Verify the composition without mounting anything:

```sh
dsh --profile pact --dump-config | grep -A8 'id: pact-dsh'
```

## Prove it without a model key

Boot the profile and read the activation record the plugin writes during
`apply()`, before any request reaches a model:

```sh
dsh --profile pact --json "Reply with exactly: PACT-OK"
cat ~/.pact/pact-harness/dsh-activation.json
```

Without a key the run stops at `MISSING_CREDENTIAL`. The activation record is
still written, and it is the proof that the plugin loaded, chose its mode and
registered the gates. A model-backed turn needs `DEEPSEEK_API_KEY` and is the
only assertion this recipe does not cover.

## What the bridge registers

| DSH surface | What PACT gets |
|---|---|
| `tools/pre-execute`, `tools/post-execute` | Tool events, and the only place a gate can refuse or correct |
| `agent/pre-step` | Prompt events, and context injection before a step |
| `agent/created` | Session start, the orchestrator persona, and a specialist persona for each teammate |
| `agent/turn-stopping` | Turn-end refusal, delivered as steering |
| `subagent/start`, `subagent/end`, `agent/status`, `session/disposed` | Lifecycle events, observed |
| `systemPrompt.section` | The orchestrator persona at order 0 |
| `commands.register` | The 13 `/pact-*` commands |
| `skills.registerProvider` | The 21 skills, read from the vendored tree with no copying |
| `tools.register` | `pact_agent`, whose `subagent_type` argument resolves to the child's name, which is what selects its persona |

`pact_agent` exists because DSH's own delegation tool has no type parameter, so
PACT's `Agent(subagent_type="pact-architect", name="architect", ...)` cannot be
followed literally. The tool takes that shape and starts an activation whose
label is the persona without its `pact-` prefix; the child then receives its
persona from the `agent/created` injection.

## Enforcement mode

```yaml
- id: pact-dsh
  config:
    mode: shadow        # off | shadow | enforce
    enforceGates:
      - merge_guard_pre
      - git_commit_check
```

In `shadow` every gate runs and journals, but only the named gates act. The two
defaults read `tool_input.command`, which DSH supplies under the identical key,
so no mapping sits between the gate and its input. Set `mode: enforce` to act on
every verdict, and expect to fix mapping bugs when you do.

## Nightly model lane

Everything above is proven without a model. The one assertion that needs one is
a live turn with the bridge mounted, and that is what the model lane exists for:

```sh
DEEPSEEK_API_KEY=... tools/model-lane.sh pact
```

It runs one `dsh --json` turn, then requires three things: exit 0, no error
event, and the bridge's activation record. The event stream, the activation
record and the newest session transcript are copied to `.model-lane/` so a
failure can be read afterwards.

CI runs the same script nightly (`.github/workflows/model-nightly.yml`) using a
`DEEPSEEK_API_KEY` repository secret, after installing DSH from npm. Without the
secret the lane fails honestly at `MISSING_CREDENTIAL`; it is scheduled rather
than per-commit for that reason.

## Uninstall

```sh
pact uninstall dsh --profile pact
```

That removes the managed block, the compiled playbook and the bundle. The
profile directory itself is left in place because it may contain more than PACT.

## Troubleshooting

- **`did not activate` on stderr** — the profile composed but a plugin failed to
  load. Run `pact doctor --adapter dsh` and check the profile's patch block.
- **A gate that never fires** — the tool name is outside the canonical
  vocabulary. `pact upstream verify` names it.
- **`PACT harness unavailable`** — the warm worker could not start. Shadow mode
  allows the call and logs; enforce mode denies. Confirm `pact serve` can start
  with the same interpreter.
