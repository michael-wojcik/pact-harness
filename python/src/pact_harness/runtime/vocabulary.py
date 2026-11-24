"""The canonical tool vocabulary the gates read.

Upstream's gates are written against Claude Code's tool names and argument
shapes. The canonical vocabulary is therefore Claude Code's vocabulary, and an
adapter's job is to translate its platform's names into it.

This matters more than a naming preference. bootstrap_gate classifies every
call into blocked, read-only, or neither, using literal name sets. A mutating
tool that arrives under a name the gate has never seen is not blocked -- it is
unclassified, which looks exactly like a pass.
"""

from __future__ import annotations

from typing import Mapping

#: Every canonical tool name the vendored gates can reason about.
CANONICAL_TOOLS: frozenset[str] = frozenset(
    {
        "Agent",
        "AskUserQuestion",
        "Bash",
        "Edit",
        "Glob",
        "Grep",
        "NotebookEdit",
        "Read",
        "SendMessage",
        "Skill",
        "TaskCreate",
        "TaskGet",
        "TaskList",
        "TaskStop",
        "TaskUpdate",
        "TodoWrite",
        "ToolSearch",
        "WebFetch",
        "WebSearch",
        "Write",
        "ExitPlanMode",
    }
)

#: Tools that mutate the filesystem or launch work, for adapter completeness checks.
MUTATING_TOOLS: frozenset[str] = frozenset(
    {"Agent", "Edit", "NotebookEdit", "Write", "Bash", "TaskCreate", "TaskUpdate", "TaskStop", "SendMessage"}
)


def canonicalize(tool_name: str | None, native_map: Mapping[str, str] | None = None) -> str | None:
    """Translate a platform tool name into the canonical vocabulary.

    An unmapped name is returned unchanged rather than dropped. The gates treat
    an unknown name as unclassified, and the adapter's completeness check is
    what turns that into a reported gap instead of a silent bypass.
    """
    if tool_name is None:
        return None
    if native_map is None:
        return tool_name
    return native_map.get(tool_name, tool_name)


#: Where injected context lands, per event kind.
INJECT_TARGETS: Mapping[str, str] = {
    "session.start": "user",
    "session.end": "user",
    "prompt.submit": "user",
    "tool.post": "step",
    "tool.fail": "step",
    "agent.start": "step",
    "agent.stop": "step",
    "agent.idle": "step",
    "task.completed": "step",
    "turn.stop": "step",
    "compact.pre": "step",
    "compact.post": "step",
}


def inject_target_for(event_kind: str) -> str:
    """The injection target one event kind should use."""
    return INJECT_TARGETS.get(event_kind, "step")
