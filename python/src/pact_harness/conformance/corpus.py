"""Representative hook frames, one or more per manifest event.

The corpus is synthetic rather than captured. Captured frames are better evidence
for a single moment, but they pin a machine's paths and session ids into the
repository, and the routing this suite checks does not depend on their values.
The captured frames upstream already ships are used by the workflow tests; this
corpus exists to cover every event and every matcher token in the manifest.

The tool names in the frames are the ones the manifest's matchers name. A frame
list that missed one would leave a registration uncovered, and the suite reports
coverage rather than silently skipping it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class CorpusFrame:
    """One frame, named, with the event it targets and the tool it names."""

    name: str
    event: str
    frame: dict[str, Any]
    tool_name: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """A JSON-shaped view for reports."""
        return {"name": self.name, "event": self.event, "tool": self.tool_name}


def _base(folder: Path, event: str, **extra: Any) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "hook_event_name": event,
        "session_id": "sess-conf",
        "cwd": str(folder),
        "agent_type": "pact-orchestrator",
    }
    frame.update(extra)
    return frame


def corpus_frames(folder: Path) -> tuple[CorpusFrame, ...]:
    """Build the frame corpus for one scratch directory."""
    target = folder / "sample.py"
    frames: list[CorpusFrame] = [
        CorpusFrame(
            "session-start",
            "SessionStart",
            _base(folder, "SessionStart", source="startup"),
        ),
        CorpusFrame("session-end", "SessionEnd", _base(folder, "SessionEnd")),
        CorpusFrame(
            "prompt-submit",
            "UserPromptSubmit",
            _base(folder, "UserPromptSubmit", prompt="implement the feature"),
        ),
        CorpusFrame("pre-compact", "PreCompact", _base(folder, "PreCompact")),
        CorpusFrame(
            "post-compact",
            "PostCompact",
            _base(folder, "PostCompact", compact_summary="summary of the session"),
        ),
        CorpusFrame(
            "pre-tool-edit",
            "PreToolUse",
            _base(
                folder,
                "PreToolUse",
                tool_name="Edit",
                tool_input={"file_path": str(target), "old_string": "a", "new_string": "b"},
                tool_use_id="toolu_edit",
            ),
            "Edit",
        ),
        CorpusFrame(
            "pre-tool-write",
            "PreToolUse",
            _base(
                folder,
                "PreToolUse",
                tool_name="Write",
                tool_input={"file_path": str(target), "content": "b"},
                tool_use_id="toolu_write",
            ),
            "Write",
        ),
        CorpusFrame(
            "pre-tool-bash",
            "PreToolUse",
            _base(
                folder,
                "PreToolUse",
                tool_name="Bash",
                tool_input={"command": "ls -la"},
                tool_use_id="toolu_bash",
            ),
            "Bash",
        ),
        CorpusFrame(
            "pre-tool-agent",
            "PreToolUse",
            _base(
                folder,
                "PreToolUse",
                tool_name="Agent",
                tool_input={
                    "subagent_type": "pact-architect",
                    "name": "architect",
                    "prompt": "design the data model",
                },
                tool_use_id="toolu_agent",
            ),
            "Agent",
        ),
        CorpusFrame(
            "pre-tool-task-update",
            "PreToolUse",
            _base(
                folder,
                "PreToolUse",
                tool_name="TaskUpdate",
                tool_input={
                    "taskId": "1",
                    "status": "in_progress",
                    "addBlockedBy": [],
                    "metadata": {},
                },
                tool_use_id="toolu_task_update",
            ),
            "TaskUpdate",
        ),
        CorpusFrame(
            "post-tool-edit",
            "PostToolUse",
            _base(
                folder,
                "PostToolUse",
                tool_name="Edit",
                tool_input={"file_path": str(target)},
                tool_response={"success": True},
                tool_use_id="toolu_edit_post",
            ),
            "Edit",
        ),
        CorpusFrame(
            "post-tool-write",
            "PostToolUse",
            _base(
                folder,
                "PostToolUse",
                tool_name="Write",
                tool_input={"file_path": str(target)},
                tool_response={"success": True},
                tool_use_id="toolu_write_post",
            ),
            "Write",
        ),
        CorpusFrame(
            "post-tool-bash",
            "PostToolUse",
            _base(
                folder,
                "PostToolUse",
                tool_name="Bash",
                tool_input={"command": "ls"},
                tool_response={"stdout": "file.txt"},
                tool_use_id="toolu_bash_post",
            ),
            "Bash",
        ),
        CorpusFrame(
            "post-tool-agent",
            "PostToolUse",
            _base(
                folder,
                "PostToolUse",
                tool_name="Agent",
                tool_input={"subagent_type": "pact-architect", "name": "architect"},
                tool_response={"agentId": "a0123456789abcdef", "status": "completed"},
                tool_use_id="toolu_agent_post",
            ),
            "Agent",
        ),
        CorpusFrame(
            "post-tool-skill",
            "PostToolUse",
            _base(
                folder,
                "PostToolUse",
                tool_name="Skill",
                tool_input={"skill": "pact-memory"},
                tool_response={"success": True},
                tool_use_id="toolu_skill",
            ),
            "Skill",
        ),
        CorpusFrame(
            "post-tool-ask",
            "PostToolUse",
            _base(
                folder,
                "PostToolUse",
                tool_name="AskUserQuestion",
                tool_input={"questions": [{"question": "Proceed?", "options": [{"label": "yes"}]}]},
                tool_response={"selections": ["yes"]},
                tool_use_id="toolu_ask",
            ),
            "AskUserQuestion",
        ),
        CorpusFrame(
            "post-tool-task-create",
            "PostToolUse",
            _base(
                folder,
                "PostToolUse",
                tool_name="TaskCreate",
                tool_input={"subject": "CODE: feature", "description": "the work"},
                tool_response={"id": "1", "status": "pending"},
                tool_use_id="toolu_task_create",
            ),
            "TaskCreate",
        ),
        CorpusFrame(
            "post-tool-task-update",
            "PostToolUse",
            _base(
                folder,
                "PostToolUse",
                tool_name="TaskUpdate",
                tool_input={"taskId": "1", "status": "completed", "metadata": {}},
                tool_response={"id": "1", "status": "completed"},
                tool_use_id="toolu_task_update_post",
            ),
            "TaskUpdate",
        ),
        CorpusFrame(
            "post-tool-failure-bash",
            "PostToolUseFailure",
            _base(
                folder,
                "PostToolUseFailure",
                tool_name="Bash",
                tool_input={"command": "false"},
                error={"message": "command failed"},
                tool_use_id="toolu_bash_fail",
            ),
            "Bash",
        ),
        CorpusFrame(
            "subagent-start",
            "SubagentStart",
            _base(folder, "SubagentStart", agent_id="a0123456789abcdef"),
        ),
        CorpusFrame(
            "subagent-stop",
            "SubagentStop",
            _base(
                folder,
                "SubagentStop",
                agent_id="a0123456789abcdef",
                last_assistant_message="HANDOFF complete",
            ),
        ),
        CorpusFrame(
            "turn-stop",
            "Stop",
            _base(folder, "Stop", stop_hook_active=False, background_tasks=[]),
        ),
        CorpusFrame(
            "task-completed",
            "TaskCompleted",
            _base(
                folder,
                "TaskCompleted",
                task_id="1",
                task_subject="CODE: feature",
                team_name="pact-team",
                teammate_name="backend",
            ),
        ),
        CorpusFrame(
            "teammate-idle",
            "TeammateIdle",
            _base(folder, "TeammateIdle", teammate_name="backend", team_name="pact-team"),
        ),
    ]
    return tuple(frames)
