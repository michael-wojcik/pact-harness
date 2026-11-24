import assert from 'node:assert/strict';
import { test } from 'node:test';

import { normalizeConfig, wire } from '../index.js';
import * as events from '../lib/events.js';

function fakeCtx() {
  const listeners = new Map();
  const warnings = [];
  return {
    warnings,
    logger: {
      info() {},
      warn(message) {
        warnings.push(message);
      },
      error() {},
    },
    on(event, listener) {
      if (!listeners.has(event)) listeners.set(event, []);
      listeners.get(event).push(listener);
      return () => {};
    },
    listenersFor(event) {
      return listeners.get(event) ?? [];
    },
    async emit(event, ...args) {
      const results = [];
      for (const listener of this.listenersFor(event)) results.push(await listener(...args));
      return results;
    },
  };
}

const envelope = (directives) => ({
  v: 1,
  directives,
  meta: { hook: 'test', event: 'tool.pre', adapter: 'dsh' },
});

const settings = (overrides = {}) =>
  normalizeConfig({ mode: 'shadow', enforceGates: ['merge_guard_pre', 'git_commit_check'], ...overrides });

function depsFor(evaluate, ctx) {
  return {
    logger: ctx.logger,
    evaluate,
    makeMessage: (text) => ({ role: 'user', content: [{ type: 'text', text }], source: { kind: 'pact-dsh' } }),
  };
}

const nextAllow = async () => ({ kind: 'allow-from-next' });

const execFor = (name, args, extra = {}) => ({
  callId: 'call-1',
  name,
  arguments: args,
  agent: { session: { id: 'sess-1', header: { cwd: '/tmp/project' } }, name: 'lead' },
  ...extra,
});

test('an enforced refusal denies the call', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings(), depsFor(async () => envelope([
    { kind: 'deny', reason: 'force-push needs approval', rule: 'merge_guard_pre' },
  ]), ctx));
  const [result] = await ctx.emit('tools/pre-execute', execFor('bash', { command: 'git push --force origin main' }), nextAllow);
  assert.deepEqual(result, { kind: 'deny', reason: 'force-push needs approval' });
});

test('a refusal outside the enforce list is shadowed', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings(), depsFor(async () => envelope([
    { kind: 'deny', reason: 'outside the worktree', rule: 'worktree_guard' },
  ]), ctx));
  const [result] = await ctx.emit('tools/pre-execute', execFor('edit', { file_path: '/tmp/a.py' }), nextAllow);
  assert.deepEqual(result, { kind: 'allow-from-next' });
  assert.match(ctx.warnings.join('\n'), /would have denied \(worktree_guard\)/);
});

test('enforce mode refuses even gates outside the list', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings({ mode: 'enforce' }), depsFor(async () => envelope([
    { kind: 'deny', reason: 'no', rule: 'worktree_guard' },
  ]), ctx));
  const [result] = await ctx.emit('tools/pre-execute', execFor('edit', {}), nextAllow);
  assert.equal(result.kind, 'deny');
});

test('an ask becomes a DSH approval request', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings(), depsFor(async () => envelope([{ kind: 'ask', reason: 'confirm' }]), ctx));
  const [result] = await ctx.emit('tools/pre-execute', execFor('bash', { command: 'ls' }), nextAllow);
  assert.deepEqual(result, { kind: 'ask', reason: 'confirm' });
});

test('no directives falls through to the host', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings(), depsFor(async () => envelope([{ kind: 'silent' }]), ctx));
  const [result] = await ctx.emit('tools/pre-execute', execFor('read', { file_path: '/tmp/a' }), nextAllow);
  assert.deepEqual(result, { kind: 'allow-from-next' });
});

test('a harness failure allows in shadow mode', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings(), depsFor(async () => { throw new Error('worker down'); }, ctx));
  const [result] = await ctx.emit('tools/pre-execute', execFor('read', {}), nextAllow);
  assert.deepEqual(result, { kind: 'allow-from-next' });
  assert.match(ctx.warnings.join('\n'), /harness unavailable/i);
});

test('a harness failure denies in enforce mode', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings({ mode: 'enforce' }), depsFor(async () => { throw new Error('worker down'); }, ctx));
  const [result] = await ctx.emit('tools/pre-execute', execFor('read', {}), nextAllow);
  assert.equal(result.kind, 'deny');
  assert.match(result.reason, /harness unavailable/i);
});

test('post-execute attaches injected context', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings(), depsFor(async () => envelope([
    { kind: 'inject', text: 'remember the handoff', target: 'step', source: 'pact' },
  ]), ctx));
  const [result] = await ctx.emit('tools/post-execute', execFor('edit', {}), { isError: false }, nextAllow);
  assert.equal(result.kind, 'accept');
  assert.equal(result.additionalContexts.length, 1);
  assert.equal(result.additionalContexts[0].content[0].text, 'remember the handoff');
});

test('post-execute can block with corrective feedback', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings(), depsFor(async () => envelope([
    { kind: 'block', reason: 'finish the handoff first' },
  ]), ctx));
  const [result] = await ctx.emit('tools/post-execute', execFor('bash', {}), { isError: false }, nextAllow);
  assert.equal(result.kind, 'block');
  assert.equal(result.feedback[0].text, 'finish the handoff first');
});

test('prompt-time context enters the step', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings(), depsFor(async () => envelope([
    { kind: 'inject', text: 'bootstrap first', target: 'user', source: 'pact' },
  ]), ctx));
  const payload = { agent: { session: { id: 's', header: { cwd: '/tmp' } } }, messages: [{ content: [] }], turn: 1, step: 1 };
  const [result] = await ctx.emit('agent/pre-step', payload, nextAllow);
  assert.equal(result.kind, 'enter');
  assert.equal(result.messages.length, 2);
  assert.equal(result.messages[1].content[0].text, 'bootstrap first');
});

test('an enforced prompt-time refusal rejects the step', async () => {
  const ctx = fakeCtx();
  wire(ctx, settings({ mode: 'enforce' }), depsFor(async () => envelope([
    { kind: 'deny', reason: 'stop', rule: 'bootstrap_gate' },
  ]), ctx));
  const payload = { agent: { session: { id: 's', header: { cwd: '/tmp' } } }, messages: [], turn: 1, step: 1 };
  const [result] = await ctx.emit('agent/pre-step', payload, nextAllow);
  assert.deepEqual(result, { kind: 'reject' });
});

test('session start injects into the agent', async () => {
  const ctx = fakeCtx();
  const injected = [];
  wire(ctx, settings(), depsFor(async () => envelope([
    { kind: 'inject', text: 'welcome to PACT', target: 'user', source: 'pact' },
  ]), ctx));
  const agent = {
    session: { id: 's', header: { cwd: '/tmp' } },
    inject: (message) => injected.push(message),
  };
  await ctx.emit('agent/created', { agent, source: 'startup' });
  assert.equal(injected.length, 1);
  assert.equal(injected[0].content[0].text, 'welcome to PACT');
});

test('turn end refusal steers in enforce mode', async () => {
  const ctx = fakeCtx();
  const steered = [];
  wire(ctx, settings({ mode: 'enforce' }), depsFor(async () => envelope([
    { kind: 'block', reason: 'handoff missing' },
  ]), ctx));
  const agent = {
    session: { id: 's', header: { cwd: '/tmp' } },
    steer: (message) => steered.push(message),
  };
  await ctx.emit('agent/turn-stopping', { agent, turn: 1 });
  assert.equal(steered.length, 1);
  assert.equal(steered[0].content[0].text, 'handoff missing');
});

test('off mode registers no listeners', () => {
  const ctx = fakeCtx();
  wire(ctx, settings({ mode: 'off' }), depsFor(async () => envelope([]), ctx));
  assert.equal(ctx.listenersFor('tools/pre-execute').length, 0);
});

test('invalid configuration is rejected', () => {
  assert.throws(() => normalizeConfig({ mode: 'sometimes' }), TypeError);
  assert.throws(() => normalizeConfig({ enforceGates: 'merge_guard_pre' }), TypeError);
  assert.throws(() => normalizeConfig({ timeoutMs: 0 }), TypeError);
});

test('defaults enforce exactly the two command gates', () => {
  const config = normalizeConfig({});
  assert.equal(config.mode, 'shadow');
  assert.deepEqual(config.enforceGates, ['merge_guard_pre', 'git_commit_check']);
});

test('tool events use the canonical vocabulary and normalise file paths', () => {
  const agent = { session: { id: 's', header: { cwd: '/tmp' } } };
  const bash = events.toolEvent('tool.pre', { callId: 'c1', name: 'bash', arguments: { command: 'ls' }, agent }, {});
  assert.equal(bash.tool.canonical, 'Bash');
  assert.equal(bash.tool.native, 'bash');
  assert.equal(bash.tool.input.command, 'ls');

  const editor = events.toolEvent('tool.pre', { callId: 'c2', name: 'str_replace_editor', arguments: { path: '/tmp/a.py' }, agent }, {});
  assert.equal(editor.tool.canonical, 'Edit');
  assert.equal(editor.tool.input.file_path, '/tmp/a.py');

  const spawn = events.toolEvent('tool.pre', { callId: 'c3', name: 'subagent', arguments: { description: 'x', prompt: 'y', name: 'arch' }, agent }, {});
  assert.equal(spawn.kind, 'agent.spawn.pre');
  assert.equal(spawn.payload.name, 'arch');
});

test('role is derived structurally', () => {
  assert.equal(events.roleOf({}, { session: { id: 's' } }), 'lead');
  assert.equal(events.roleOf({}, { session: { id: 's' }, parentSession: 'p' }), 'teammate');
  const subagents = { rootOf: (agent) => (agent === 'root' ? 'root' : 'other') };
  assert.equal(events.roleOf({ subagents }, 'root'), 'lead');
  assert.equal(events.roleOf({ subagents }, 'child'), 'teammate');
});

test('event ids are contract-safe', () => {
  const id = events.eventId('session/with spaces:and:colons');
  assert.match(id, /^[A-Za-z0-9][A-Za-z0-9_-]*$/);
  assert.ok(id.length <= 64);
});
