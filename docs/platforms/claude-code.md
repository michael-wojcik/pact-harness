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

## Bridged mode

Bridged mode replaces every hook command with a call into the harness, so both
platforms run one code path. It is opt-in and it owns a directory:

```sh
pact install claude-code --mode bridged --target ~/.pact/claude-bridged
```

That copies the plugin, rewrites all 34 commands in `hooks.json` to

```text
python3 -m pact_harness hook <module> --adapter claude-code --passthrough --plugin-root "${CLAUDE_PLUGIN_ROOT}"
```

and writes a manifest. The copy is a real copy, not a reference into the pin, so
a later submodule move cannot leave the installed tree pointing at something
else.

### The gate

The install refuses to happen unless **byte parity currently holds**: for every
one of the 48 corpus cells, the shim's stdout and exit code must equal running
the hook directly. That is re-run at install time rather than trusted from a
document, which is why install takes about ten seconds.

The comparison is fair: each path runs in its own freshly built tree, and each
tree's paths are normalised to a placeholder before the bytes are compared.
Without that, the first run would mutate state the second one sees, or every
output would differ by the temp directory's name.

Check it yourself at any time:

```sh
pact conformance --parity     # 48 cells across 24 frames
```

### Requirements and removal

The shim needs `pact_harness` importable by the shell Claude Code uses
(`pip install pact-harness`, or the checkout on `PYTHONPATH`). Then point
Claude Code at the printed target directory instead of the vendored plugin.

```sh
pact uninstall claude-code --target ~/.pact/claude-bridged
```

Removal refuses a directory without the manifest this installer writes, so it
cannot delete a tree it did not make.

## Checking the adapter

```sh
pact capabilities --adapter claude-code   # T3, the reference tier
pact conformance                          # 34/34 registrations, 0 failures
pact doctor --adapter claude-code
```
