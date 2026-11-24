# Changelog

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
- A conformance suite covering all 34 registrations.

### Why

A port would have to re-derive ~90k lines and every implicit contract, and would
be stale within weeks. Treating the hook protocol as the ABI keeps one
implementation, one regression oracle, and an upstream bump that is measured
rather than suffered.
