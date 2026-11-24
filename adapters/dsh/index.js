/**
 * PACT for DeepSeek Harness.
 *
 * The plugin is a bridge, not a port. It maps DSH's extension points onto
 * pact.event.v1, asks the harness what PACT decided, and renders that answer
 * back into DSH's own decision types. All of PACT's policy lives in the
 * vendored Python; nothing in this file reimplements a gate.
 *
 * Failure policy. If the harness cannot be reached, shadow mode allows the
 * call and logs loudly, because a broken bridge must not wedge an editor.
 * Enforce mode denies instead, because silently allowing while claiming
 * enforcement is the one failure the harness exists to prevent.
 */

import { writeFileSync, mkdirSync, readFileSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import * as decisions from './lib/decisions.js';
import * as events from './lib/events.js';
import { HarnessWorker } from './lib/worker.js';

export const name = 'pact-dsh';

/** Modes the bridge accepts. */
export const MODES = Object.freeze(['off', 'shadow', 'enforce']);

/** The gates enforced when a fresh install runs in shadow mode. */
export const DEFAULT_ENFORCE_GATES = Object.freeze(['merge_guard_pre', 'git_commit_check']);

const DEFAULTS = Object.freeze({
  mode: 'shadow',
  enforceGates: DEFAULT_ENFORCE_GATES,
  timeoutMs: 60000,
});

/** Validate and fill in a plugin config. */
export function normalizeConfig(config = {}, env = process.env) {
  const merged = { ...DEFAULTS, ...config };
  if (!MODES.includes(merged.mode)) {
    throw new TypeError(
      `pact-dsh: mode must be one of ${MODES.join(', ')}, got ${JSON.stringify(merged.mode)}`,
    );
  }
  if (!Array.isArray(merged.enforceGates) || merged.enforceGates.some((gate) => typeof gate !== 'string')) {
    throw new TypeError('pact-dsh: enforceGates must be an array of gate names');
  }
  if (merged.timeoutMs !== undefined && (!Number.isFinite(merged.timeoutMs) || merged.timeoutMs <= 0)) {
    throw new TypeError('pact-dsh: timeoutMs must be a positive number');
  }
  return {
    ...merged,
    python: config.python ?? env.PACT_DSH_PYTHON ?? env.PACT_PYTHON ?? 'python3',
    pluginRoot: config.pluginRoot ?? env.PACT_PLUGIN_ROOT ?? undefined,
    // The same default the Python side resolves: a DSH install keeps its state
    // under ~/.pact, separate from Claude Code's ~/.claude. A record written to
    // the temporary directory instead would be invisible to pact doctor.
    stateRoot: config.stateRoot ?? env.PACT_HOME ?? path.join(os.homedir(), '.pact'),
    playbook: config.playbook ?? env.PACT_DSH_PLAYBOOK ?? undefined,
    socketPath: config.socketPath,
  };
}

/** Build the context-message factory, preferring the host's own helper. */
export async function loadMessageFactory(logger) {
  try {
    const llm = await import('@deepseek-ai/dsh-llm');
    if (typeof llm.createUserMessage === 'function') {
      return (text) =>
        llm.createUserMessage({
          content: [{ type: 'text', text }],
          source: { kind: 'pact-dsh' },
        });
    }
  } catch (error) {
    logger?.warn?.(`pact-dsh: using plain context messages: ${error?.message ?? error}`);
  }
  return (text) => ({
    role: 'user',
    content: [{ type: 'text', text }],
    source: { kind: 'pact-dsh' },
  });
}

/** Read the compiled playbook the installer wrote, or null when absent. */
export function loadPlaybook(playbookPath) {
  if (!playbookPath) return null;
  const document = JSON.parse(readFileSync(playbookPath, 'utf8'));
  if (!document || typeof document !== 'object' || Array.isArray(document)) {
    throw new Error('playbook is not an object');
  }
  return document;
}

/** Read an optional service without tripping cordis's inject requirement. */
export function safeService(ctx, name) {
  try {
    if (typeof ctx?.get === 'function') return ctx.get(name);
    return ctx?.[name];
  } catch {
    return undefined;
  }
}

/**
 * Register the orchestrator persona as a system prompt section.
 *
 * This is the closest DSH analogue of Claude Code's --agent: the persona joins
 * the assembly at order 0, the deployment-persona convention. It is not
 * editable by the user and not loaded from the workspace, because PACT's own
 * threat model depends on the persona being authoritative rather than guidance.
 */
export function registerPersona(ctx, settings, logger) {
  if (!settings.playbook) {
    logger?.info?.('pact-dsh: no playbook configured; the orchestrator persona is not registered');
    return null;
  }
  let playbook;
  try {
    playbook = loadPlaybook(settings.playbook);
  } catch (error) {
    logger?.warn?.(`pact-dsh: cannot read the playbook: ${error?.message ?? error}`);
    return null;
  }
  const systemPrompt = safeService(ctx, 'systemPrompt');
  if (!systemPrompt || typeof systemPrompt.section !== 'function') {
    logger?.warn?.('pact-dsh: system prompt service unavailable; the orchestrator persona is not registered');
    return null;
  }
  const personas = Array.isArray(playbook.personas) ? playbook.personas : [];
  const orchestrator = personas.find((persona) => persona?.name === 'pact-orchestrator');
  if (!orchestrator || typeof orchestrator.body !== 'string') {
    logger?.warn?.('pact-dsh: the playbook has no orchestrator persona');
    return null;
  }
  const notes = typeof playbook.platform_notes === 'string' ? playbook.platform_notes : '';
  const text = `${orchestrator.body}\n\n${notes}`.trim();
  const dispose = systemPrompt.section({ name: 'pact:orchestrator', order: 0, text });
  if (typeof dispose === 'function' && typeof ctx.effect === 'function') {
    ctx.effect(() => dispose);
  }
  logger?.info?.(`pact-dsh: registered the orchestrator persona (${text.length} bytes)`);
  return text;
}

/** Write a startup record so a boot can be proven without a model. */
export function recordActivation(settings, extra = {}) {
  const root = settings.stateRoot ?? path.join(os.homedir(), '.pact');
  const directory = path.join(String(root), 'pact-harness');
  mkdirSync(directory, { recursive: true });
  const record = {
    schemaVersion: 1,
    activatedAt: new Date().toISOString(),
    pid: process.pid,
    mode: settings.mode,
    enforceGates: [...settings.enforceGates],
    ...extra,
  };
  writeFileSync(path.join(directory, 'dsh-activation.json'), JSON.stringify(record, null, 2));
  return record;
}

/** The bridge's dependency surface, injectable so it can be tested. */
export function dependenciesFor(ctx, worker) {
  const logger = ctx?.logger ?? {};
  return {
    logger,
    evaluate: (event) => worker.evaluate(event),
    makeMessage: (text) => ({
      role: 'user',
      content: [{ type: 'text', text }],
      source: { kind: 'pact-dsh' },
    }),
  };
}

/** Ask the harness, converting an infrastructure failure into a policy answer. */
async function ask(deps, settings, event) {
  try {
    return await deps.evaluate(event);
  } catch (error) {
    const message = `PACT harness unavailable: ${error?.message ?? error}`;
    deps.logger?.warn?.(`pact-dsh: ${message}`);
    if (settings.mode === 'enforce') {
      return { v: 1, directives: [{ kind: 'deny', reason: message, rule: 'pact-harness' }] };
    }
    return { v: 1, directives: [{ kind: 'notify', level: 'warning', text: message }] };
  }
}

/** Register the bridge's listeners against a cordis context. */
export function wire(ctx, settings, deps) {
  if (!ctx || typeof ctx.on !== 'function') {
    throw new TypeError('pact-dsh: a cordis context with on() is required');
  }
  if (settings.mode === 'off') return;

  ctx.on('tools/pre-execute', async (exec, next) => {
    const event = events.toolEvent('tool.pre', exec, ctx);
    const envelope = await ask(deps, settings, event);
    const { decision, shadowed } = decisions.decidePre(envelope.directives ?? [], settings);
    if (shadowed) deps.logger?.warn?.(`pact-dsh: ${shadowed}`);
    return decision ?? next();
  });

  ctx.on('tools/post-execute', async (exec, result, next) => {
    const event = events.toolEvent(result?.isError ? 'tool.fail' : 'tool.post', exec, ctx, result);
    const envelope = await ask(deps, settings, event);
    const directives = envelope.directives ?? [];
    const block = decisions.blockOf(directives);
    const messages = decisions
      .injectionsOf(directives)
      .concat(decisions.noticesOf(directives))
      .map((directive) => deps.makeMessage(directive.text));
    if (block) {
      return {
        kind: 'block',
        feedback: [{ type: 'text', text: block.reason }],
        additionalContexts: messages,
      };
    }
    if (messages.length) return { kind: 'accept', additionalContexts: messages };
    return next();
  });

  ctx.on('agent/pre-step', async (payload, next) => {
    const event = events.promptEvent(payload, ctx);
    const envelope = await ask(deps, settings, event);
    const directives = envelope.directives ?? [];
    const refusal = decisions.refusalOf(directives);
    if (refusal && decisions.shouldEnforce(refusal.rule, settings)) {
      return { kind: 'reject' };
    }
    if (refusal) {
      deps.logger?.warn?.(
        `pact-dsh: PACT would have rejected the step (${refusal.rule ?? 'unknown gate'}): ${refusal.reason}`,
      );
    }
    const messages = decisions
      .injectionsOf(directives)
      .concat(decisions.noticesOf(directives))
      .map((directive) => deps.makeMessage(directive.text));
    if (messages.length) {
      const base = Array.isArray(payload?.messages) ? payload.messages : [];
      return { kind: 'enter', messages: [...base, ...messages] };
    }
    return next();
  });

  ctx.on('agent/created', async (payload) => {
    const event = events.lifecycleEvent('session.start', payload?.agent, ctx, {
      source: payload?.source,
    });
    const envelope = await ask(deps, settings, event);
    const directives = envelope.directives ?? [];
    const messages = decisions
      .injectionsOf(directives)
      .concat(decisions.noticesOf(directives))
      .map((directive) => deps.makeMessage(directive.text));
    for (const message of messages) {
      payload?.agent?.inject?.(message);
    }
  });

  ctx.on('agent/turn-stopping', async (payload) => {
    const event = events.lifecycleEvent('turn.stop', payload?.agent, ctx);
    const envelope = await ask(deps, settings, event);
    const block = decisions.blockOf(envelope.directives ?? []);
    if (block && settings.mode !== 'shadow') {
      payload?.agent?.steer?.(deps.makeMessage(block.reason));
    } else if (block) {
      deps.logger?.warn?.(`pact-dsh: PACT would have refused the turn end: ${block.reason}`);
    }
  });

  ctx.on('agent/status', async (payload) => {
    if (payload?.status !== 'idle') return;
    const event = events.lifecycleEvent('agent.idle', payload?.agent, ctx);
    await ask(deps, settings, event);
  });

  ctx.on('session/disposed', async (session) => {
    await ask(deps, settings, events.sessionEndEvent(session, ctx));
  });

  ctx.on('subagent/start', async (info) => {
    const event = {
      v: 1,
      id: events.eventId(`${info?.id}:agent.start`),
      kind: 'agent.start',
      at: new Date().toISOString(),
      adapter: 'dsh',
      session: { id: events.sanitizeId(info?.parentSessionId, 'session'), cwd: process.cwd() },
      actor: { role: 'unknown' },
      payload: { provider: info?.provider, runId: String(info?.runId ?? '') },
    };
    await ask(deps, settings, event);
  });

  ctx.on('subagent/end', async (info) => {
    const event = {
      v: 1,
      id: events.eventId(`${info?.id}:agent.stop`),
      kind: 'agent.stop',
      at: new Date().toISOString(),
      adapter: 'dsh',
      session: { id: events.sanitizeId(info?.parentSessionId, 'session'), cwd: process.cwd() },
      actor: { role: 'unknown' },
      payload: { provider: info?.provider, stopReason: String(info?.stopReason ?? '') },
    };
    await ask(deps, settings, event);
  });
}

/** Cordis entry point. */
export function apply(ctx, config = {}) {
  const settings = normalizeConfig(config);
  if (settings.mode === 'off') {
    ctx.logger?.info?.('pact-dsh: off');
    return;
  }
  const worker = new HarnessWorker({
    python: settings.python,
    pluginRoot: settings.pluginRoot,
    stateRoot: settings.stateRoot,
    socketPath: settings.socketPath,
    timeoutMs: settings.timeoutMs,
    logger: ctx.logger,
  });
  if (typeof ctx.effect === 'function') {
    ctx.effect(() => () => worker.stop(), 'pact-dsh worker');
  }
  const deps = dependenciesFor(ctx, worker);
  loadMessageFactory(ctx.logger)
    .then((makeMessage) => {
      deps.makeMessage = makeMessage;
    })
    .catch(() => undefined);
  try {
    recordActivation(settings, {
      pluginRoot: settings.pluginRoot ?? null,
      python: settings.python,
      playbook: settings.playbook ?? null,
    });
    ctx.logger?.info?.(`pact-dsh: ${settings.mode} mode, enforcing ${settings.enforceGates.join(', ') || 'nothing'}`);
  } catch (error) {
    ctx.logger?.warn?.(`pact-dsh: could not write the activation record: ${error?.message ?? error}`);
  }
  wire(ctx, settings, deps);
  registerPersona(ctx, settings, ctx.logger);
}
