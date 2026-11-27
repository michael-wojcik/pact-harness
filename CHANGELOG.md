# Changelog

## Unreleased

- The release lane splits publishing into two jobs with their own environments:
  PyPI under the pypi environment and npm under the npm environment. Both use
  trusted publishing (OIDC); no registry token is stored. This is the path npm
  points at while it restricts 2FA-bypass tokens (account changes August 2026,
  direct publishing January 2027).

## v0.1.4: first npm release

- The bridge package is published to npm as @pact-harness/dsh, from the release
  lane, through trusted publishing (OIDC). No registry token is stored, and this
  is the path npm recommends as it restricts 2FA-bypass tokens.
- The release lane now has three jobs: verify-and-publish, publish-pypi
  (environment pypi) and publish-npm (environment npm). Both publish jobs are
  gated on repository variables so an ordinary tag release is unaffected.

## v0.1.3: first PyPI release

- pact-harness is published on PyPI through GitHub OIDC trusted publishing: no
  long-lived token is stored, and a version tag publishes to both PyPI and
  GitHub from the release lane.
- Repository hardening: main requires the contract lane (strict) and a pull
  request, force pushes and deletions are blocked, and secret scanning with push
  protection, Dependabot security updates and private vulnerability reporting are
  enabled.
- The bridge package is publish-ready; its npm publication waits on an automation
  token for the @pact-harness scope.

## v0.1.2: license, portable manifest, rewritten history

- The MIT license is now in the repository, and the bridge package declares it
  for npm consumers.
- The committed surface manifest no longer records the machine it was built on.
  `pact upstream inspect --portable` writes a placeholder, and the baseline was regenerated with it.
- Repository history was rewritten so no commit carries a local machine path.
  Commit hashes changed, and the v0.1.1 tag does not exist in the new history;
  this release supersedes it.
- Nightly model lane: an Ubuntu runner installs DSH from npm, installs the bridge
  into a headless profile, and runs a live model turn. First run green: dsh exit
  0, no error events, activation record written.

## v0.1.1: session-start fix and CI hardening

The first release's CI runs found three defects that local runs could not, all
fixed here.

- **A real DSH session aborted at session start** with "message "undefined" is
  already pending". The bridge prefers the host's createUserMessage, which
  assigns each message an id, but that import fails from the linked plugin
  directory, so it fell back to an id-less message; DSH's inbox rejects two
  pending messages sharing an id. The fallback now carries a unique id.
- **The bridged shim resolved its own interpreter** instead of inheriting its
  own, so a hook could report a different Python than a direct invocation.
- **Byte parity shared $HOME between the two runs**, so the first created the
  user-global links the second reported as present. Each path now has its own
  home, and HOME joins the normalised paths.

Also: the release lane publishes assets with the GitHub CLI and leaves existing
assets alone rather than deleting and re-uploading them, and the contract lane
installs upstream's hash-pinned environment rather than guessing at its test
dependencies.

## v0.1.0: first harness release

### Summary

PACT becomes a platform-neutral harness: the Claude Code plugin's own hooks run
unmodified behind a versioned contract, with adapters for DeepSeek Harness and
for any MCP client, plus a measured pipeline for absorbing upstream releases.

### What landed

- `pact.event.v1` and `pact.directive.v1`: hand-validated models, published JSON
  Schemas, and a decoder covering every output shape the 28 vendored modules
  emit.
- A hook runtime with a cold transport and a warm fork transport, driven by a
  capability model that derives tiers T0–T3.
- DeepSeek Harness: a cordis bridge for tool, prompt, agent and session events;
  shadow/enforce policy; the orchestrator persona as a system prompt section; 13
  commands; 21 skills; specialist personas for teammates.
- A portable MCP adapter with advisory tools and a memory backend seam.
- A playbook compiler that resolves plugin-relative references and replaces
  Claude's argument placeholder.
- An upstream pipeline: surface extraction, L0–L4 classification, verification,
  release-distance checking, and a sync script that never commits.
- A conformance suite covering all 34 registrations, plus a byte-parity proof
  (48 cells, stdout and exit code) that gates bridged Claude Code mode: the
  installer rewrites all 34 hook commands to call the harness, and refuses to
  install unless the bridged run is byte-identical to a direct one.
- A corpus verdict matrix (48 cells, 28 hooks) recorded for the pin and replayed
  after an upstream bump, and an upstream suite runner whose 19 macOS-only
  failures are inventoried with reasons rather than hidden.
- An advisory installer for §AGENTS.md§ platforms and an MCP client surface whose
  tools say, in their own text, that nothing on that path can enforce a verdict.
- §pact dev link§, §pact hook§, §pact migrate-state§, and a doctor that prints the
  hook-module inventory, a derived applicability table, and the last activation
  record.

### Why

A port would have to re-derive ~90k lines and every implicit contract, and would
be stale within weeks. Treating the hook protocol as the ABI keeps one
implementation, one regression oracle, and an upstream bump that is measured
rather than suffered.