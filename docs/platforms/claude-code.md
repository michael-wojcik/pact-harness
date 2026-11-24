# PACT on Claude Code

Claude Code is the platform PACT was written for. The harness does not replace
the plugin, intercept it, or change how it behaves.

## What the harness adds

- The same pinned core, available to other platforms.
- `pact doctor --adapter claude-code`, which reports the live registry, the
  reference tier and the interpreter.
- `pact conformance`, which runs all 34 registrations and asserts that the
  adapter re-delivers each frame unchanged and that every hook's output decodes.
- A `pact` command that passes through to the upstream launcher.

## The `pact` command

Upstream's `bin/pact` is `exec claude --agent PACT:pact-orchestrator "$@"`, and
the README suggests symlinking it onto `PATH`. The harness owns the name and
keeps that behaviour:

```sh
pact                      # same as upstream's launcher
pact --resume <session>   # flags pass through
pact doctor               # harness verbs are new surface
```

## What is not built

A bridged install — replacing the plugin's `hooks.json` with harness shims — is
designed but not shipped. The adapter and the conformance suite exist, and the
conformance suite is the gate that would have to pass first; until it is wired,
Claude Code keeps using its own hooks. That is also why the default install
here is "leave the plugin alone": the zero-risk path is the only one currently
offered.

## Checking the adapter

```sh
pact capabilities --adapter claude-code   # T3, the reference tier
pact conformance                          # 34/34 registrations, 0 failures
pact doctor --adapter claude-code
```
