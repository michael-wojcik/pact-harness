# PACT over MCP

For platforms with no hook surface, the harness exposes itself as tools the model
calls. This is the honest advisory tier: nothing on this path can veto a tool
call, and every result says so.

## Configure the client

Any MCP client that speaks stdio can launch the server:

```json
{
  "mcpServers": {
    "pact": {
      "command": "pact",
      "args": ["mcp", "serve"]
    }
  }
}
```

From a checkout without installing, use
`python3 -m pact_harness mcp serve` with `PYTHONPATH=python/src`, or the
`python/bin/pact` launcher.

## Tools

| Tool | Arguments | Returns |
|---|---|---|
| `pact_event` | `{ event }` | The directive envelope for one `pact.event.v1` document |
| `pact_gate` | `{ tool: { canonical, native, input }, session?, actor? }` | `ALLOW`, `ASK` or `DENY` with the gate's rule, plus the envelope |
| `pact_status` | — | Adapter, tier, pinned version, registry size, paths |
| `pact_memory_status` | — | The memory backend's location and whether a store exists |
| `pact_memory_save` | `{ memory }` | The backend's own save result |
| `pact_memory_search` | `{ query, limit? }` | The backend's own search result |
| `pact_journal_append` | `{ type, data?, session_id? }` | The journal CLI's own result |
| `pact_journal_read` | `{ type?, since?, session_id? }` | The events as a JSON array |

## What `pact_gate` actually promises

It answers the question "would PACT refuse this?" and then states plainly that
the platform will not enforce the answer. A model that honours a `DENY` is
following advice; a platform that ignores it is not violating a contract,
because none was offered here. `pact_status` reports tier `T0` for the same
reason.

## Journal

The journal tools give a foreign agent a namespace of its own under the harness
state root rather than writing into a PACT session directory. Writing there
would make harness events indistinguishable from a real session's history, and
every consumer of that journal would start reading rows nothing asked for.
Event types and their required fields belong to PACT, so the tools proxy the CLI
and report its refusal instead of inventing a vocabulary.

## Memory

The memory tools go through a backend seam. The only backend that ships shells
out to PACT's own memory CLI, which is the route that skill declares as the only
one — a caller does not select the store, the CLI resolves it. The harness never
opens `memory.db` and keeps no cache.
