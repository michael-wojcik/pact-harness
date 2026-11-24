# PACT on an AGENTS.md platform

For a platform with no interception hooks — Cursor, Codex CLI, a custom agent —
PACT runs in a declared advisory mode. The model reads the policy, calls the
tools, and is expected to honour them.

## Install

```sh
pact install generic --target .
pact mcp serve            # or point your client at this command
```

That writes one managed section into `AGENTS.md` and links the 21 skills into
`~/.agents/skills`. Nothing is copied: the links point at the vendored tree, so
an upstream bump changes what the model reads without reinstalling anything.
`--target` picks the project, `--skills-root` the shared skills root,
`--no-skills` writes only the section.

## What the model is told

The section says what is true here:

> It answers ALLOW, ASK or DENY with the rule that fired. Honour a DENY
> yourself: nothing on this platform will enforce it for you.

That is the tier in one sentence. `pact_gate` reports PACT's judgement, and the
advisory claim is repeated by `pact_status` (tier T0) so an auditor can see it
without reading the prompt.

## Remove

```sh
pact uninstall generic --target .
```

Removes the section and only the links this installer made — it checks where
each link points before deleting it, so a skill you replaced with your own
directory survives.
