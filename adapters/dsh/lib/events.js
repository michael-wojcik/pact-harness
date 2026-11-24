/**
 * Normalising DSH extension-point payloads into pact.event.v1.
 *
 * Every field here is named deliberately. A platform that is not Claude Code
 * has no inherited frame, so a field this module forgets is a field the
 * vendored hooks will not see -- which is how a gate quietly stops applying.
 */

import { createHash } from 'node:crypto';

import { canonicalize, normalizeArguments } from './tools.js';

const ID_ALLOWED = /[^A-Za-z0-9_-]/g;

/** Force any identifier into the contract's id alphabet. */
export function sanitizeId(value, fallback = 'unknown') {
  const text = String(value ?? '').replace(ID_ALLOWED, '_').slice(0, 64);
  if (!text) return fallback;
  return /[A-Za-z0-9]/.test(text[0]) ? text : 'x' + text;
}

/** A deterministic event id from a seed. */
export function eventId(seed) {
  const digest = createHash('sha256').update(String(seed)).digest('hex').slice(0, 16);
  return `evt_${digest}`;
}

/** The session id a DSH agent carries. */
export function sessionIdOf(agent, fallback = 'session') {
  // The session is agent.session, and its id is inside it: an earlier version of
  // this bridge read agent.sessionId, which does not exist on a DSH Agent, and
  // the resulting undefined silently disabled every session-scoped behaviour.
  const id = agent?.session?.id ?? agent?.sessionId;
  return sanitizeId(id, fallback);
}

/** The working directory a DSH agent carries. */
export function cwdOf(agent, fallback = process.cwd()) {
  return agent?.session?.header?.cwd ?? agent?.cwd ?? fallback;
}

/** Whether this agent is the tree root, and therefore the team lead. */
export function roleOf(ctx, agent) {
  if (!agent) return 'unknown';
  const subagents = ctx?.subagents;
  if (subagents && typeof subagents.rootOf === 'function') {
    try {
      return subagents.rootOf(agent) === agent ? 'lead' : 'teammate';
    } catch {
      // Fall through to the structural answer rather than guessing at runtime.
    }
  }
  return agent?.parentSession || agent?.parentSessionId ? 'teammate' : 'lead';
}

/** The actor block for one event. */
export function actorOf(ctx, agent) {
  const role = roleOf(ctx, agent);
  const name = agent?.name;
  const actor = { role };
  if (name) actor.name = sanitizeId(name, 'agent');
  if (role === 'lead') actor.type = 'pact-orchestrator';
  else if (role === 'teammate') actor.type = name ? sanitizeId(name, 'agent') : 'pact-teammate';
  return actor;
}

/** The session block for one event. */
export function sessionOf(agent, extra = {}) {
  const session = { id: sessionIdOf(agent), cwd: cwdOf(agent) };
  if (extra.project_dir) session.project_dir = String(extra.project_dir);
  if (extra.transcript_path) session.transcript_path = String(extra.transcript_path);
  return session;
}

/** Extract plain text from a list of DSH content blocks. */
export function textOfContent(content) {
  if (!Array.isArray(content)) return '';
  return content
    .filter((block) => block && block.type === 'text' && typeof block.text === 'string')
    .map((block) => block.text)
    .join('\n');
}

/**
 * Build a tool event.
 *
 * A call to the delegation tool is reported as agent.spawn.pre rather than
 * tool.pre: the gates distinguish "delegate to a specialist" from "run a tool",
 * and collapsing them would apply tool gates to delegation.
 */
export function toolEvent(kind, exec, ctx, result) {
  const native = typeof exec?.name === 'string' ? exec.name : 'Unknown';
  const canonical = canonicalize(native);
  const args = normalizeArguments(canonical, exec?.arguments);
  const agent = exec?.agent;
  const spawn = canonical === 'Agent';
  const event = {
    v: 1,
    id: eventId(`${sessionIdOf(agent)}:${kind}:${exec?.callId ?? ''}:${native}`),
    kind: spawn && kind === 'tool.pre' ? 'agent.spawn.pre' : kind,
    at: new Date().toISOString(),
    adapter: 'dsh',
    session: sessionOf(agent, { project_dir: cwdOf(agent) }),
    actor: actorOf(ctx, agent),
  };
  if (spawn && event.kind === 'agent.spawn.pre') {
    event.payload = {
      subagent_type: args.subagent_type ?? args.type,
      name: args.name,
      prompt: args.prompt,
      description: args.description,
      run_in_background: args.run_in_background,
    };
    for (const key of Object.keys(event.payload)) {
      if (event.payload[key] === undefined) delete event.payload[key];
    }
  } else {
    const tool = {
      canonical,
      native,
      input: args,
      call_id: exec?.callId ? sanitizeId(exec.callId) : undefined,
      parent_call_id: exec?.parent?.callId ? sanitizeId(exec.parent.callId) : undefined,
      is_sub_dispatch: Boolean(exec?.parent),
    };
    if (result) {
      if (result.isError) tool.error = { message: String(result.error?.message ?? result.error ?? '') };
      else if (result.value !== undefined) tool.result = { value: result.value };
    }
    for (const key of Object.keys(tool)) if (tool[key] === undefined) delete tool[key];
    event.tool = tool;
  }
  return event;
}

/** Build a prompt event from an agent/pre-step payload. */
export function promptEvent(payload, ctx) {
  const agent = payload?.agent;
  const messages = Array.isArray(payload?.messages) ? payload.messages : [];
  const prompt = messages
    .map((message) => textOfContent(message?.content))
    .filter(Boolean)
    .join('\n');
  return {
    v: 1,
    id: eventId(`${sessionIdOf(agent)}:prompt:${payload?.turn ?? 0}:${payload?.step ?? 0}`),
    kind: 'prompt.submit',
    at: new Date().toISOString(),
    adapter: 'dsh',
    session: sessionOf(agent, { project_dir: cwdOf(agent) }),
    actor: actorOf(ctx, agent),
    payload: prompt ? { prompt } : {},
  };
}

/** Build a lifecycle event. */
export function lifecycleEvent(kind, agent, ctx, payload = {}) {
  const event = {
    v: 1,
    id: eventId(`${sessionIdOf(agent)}:${kind}:${payload.source ?? ''}`),
    kind,
    at: new Date().toISOString(),
    adapter: 'dsh',
    session: sessionOf(agent, { project_dir: cwdOf(agent) }),
    actor: actorOf(ctx, agent),
  };
  if (Object.keys(payload).length) event.payload = payload;
  return event;
}

/** Build a session-end event from a disposed session. */
export function sessionEndEvent(session, ctx) {
  return {
    v: 1,
    id: eventId(`${sanitizeId(session?.id)}:session.end`),
    kind: 'session.end',
    at: new Date().toISOString(),
    adapter: 'dsh',
    session: {
      id: sanitizeId(session?.id),
      cwd: session?.header?.cwd ?? process.cwd(),
    },
    actor: { role: 'unknown' },
  };
}

/** Build a task-completed event from a team task projection. */
export function taskEvent(task, ctx) {
  return {
    v: 1,
    id: eventId(`${task?.id}:task.completed:${task?.revision ?? 0}`),
    kind: 'task.completed',
    at: new Date().toISOString(),
    adapter: 'dsh',
    session: { id: 'team-task', cwd: process.cwd() },
    actor: { role: 'unknown' },
    task: {
      id: sanitizeId(task?.id),
      subject: task?.subject,
      team_name: task?.teamName,
      teammate_name: task?.ownerName ?? task?.owner,
      status: task?.status,
    },
  };
}
