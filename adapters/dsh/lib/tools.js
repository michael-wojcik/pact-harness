/**
 * The canonical tool vocabulary.
 *
 * PACT's gates are written against Claude Code's tool names and argument
 * shapes, so the canonical vocabulary is Claude Code's. An unmapped name is
 * passed through unchanged rather than dropped: the gates treat an unknown
 * name as unclassified, and `pact doctor` is where that gets reported. A name
 * silently dropped here would be a gate silently not running.
 */

export const CANONICAL_TOOLS = Object.freeze({
  bash: 'Bash',
  pwsh: 'Bash',
  read: 'Read',
  read_image: 'Read',
  write: 'Write',
  edit: 'Edit',
  str_replace_editor: 'Edit',
  apply_patch: 'Edit',
  glob: 'Glob',
  grep: 'Grep',
  subagent: 'Agent',
  subagent_fork: 'Agent',
  spawn_teammate: 'Agent',
  pact_agent: 'Agent',
  team_task_create: 'TaskCreate',
  team_task_update: 'TaskUpdate',
  team_task_list: 'TaskList',
  team_task_get: 'TaskGet',
  send_message: 'SendMessage',
  skill: 'Skill',
  ask_user_question: 'AskUserQuestion',
  todo_write: 'TodoWrite',
  exit_plan_mode: 'ExitPlanMode',
  run_code: 'RunCode',
});

/** Translate one DSH tool name into the canonical vocabulary. */
export function canonicalize(name) {
  if (typeof name !== 'string' || name === '') return 'Unknown';
  return CANONICAL_TOOLS[name] ?? name;
}

/** Tools whose target is a single file. */
const FILE_TOOLS = new Set(['Edit', 'Read', 'Write']);

/**
 * Normalise call arguments so a gate always finds tool_input.file_path.
 *
 * DSH's file tools do not agree on the key: edit/write/read use file_path and
 * str_replace_editor uses path. A gate that reads only one spelling declines to
 * fire on the other, which is indistinguishable from the gate passing.
 */
export function normalizeArguments(canonical, rawArgs) {
  const args = rawArgs && typeof rawArgs === 'object' ? { ...rawArgs } : {};
  if (FILE_TOOLS.has(canonical) && typeof args.file_path !== 'string') {
    if (typeof args.path === 'string') args.file_path = args.path;
  }
  return args;
}

/** The single file a call targets, when it targets exactly one. */
export function filePathOf(args) {
  return args && typeof args.file_path === 'string' ? args.file_path : undefined;
}
