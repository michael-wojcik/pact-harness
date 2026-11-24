import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import { loadPlaybook, normalizeConfig, registerCommands, registerPersona, wire } from '../index.js';
import * as events from '../lib/events.js';

export function fakeCtx(services = {}) {
  const listeners = new Map();
  const warnings = [];
  return {
    warnings,
    infos: [],
    logger: {
      info(message) {
        this.infos = this.infos ?? [];
      },
      warn(message) {
        warnings.push(message);
      },
      error() {},
    },
    get(name) {
      return services[name];
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

test('the optional team service is read through ctx.get, never the property', () => {
  const subagents = { rootOf: (agent) => (agent === 'root' ? 'root' : 'other') };
  const services = { subagents };
  const ctx = {
    get(name) {
      return services[name];
    },
  };
  assert.equal(events.roleOf(ctx, 'root'), 'lead');
  assert.equal(events.roleOf(ctx, 'child'), 'teammate');
});

test('a cordis-style property read failure does not break role detection', () => {
  const ctx = {
    get() {
      return undefined;
    },
    get subagents() {
      throw new Error('cannot get property "subagents" without inject');
    },
  };
  assert.equal(events.roleOf(ctx, { session: { id: 's' } }), 'lead');
});

test('event ids are contract-safe', () => {
  const id = events.eventId('session/with spaces:and:colons');
  assert.match(id, /^[A-Za-z0-9][A-Za-z0-9_-]*$/);
  assert.ok(id.length <= 64);
});

function writePlaybook(document) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'pact-playbook-test-'));
  const target = path.join(directory, 'playbook.json');
  fs.writeFileSync(target, JSON.stringify(document));
  return target;
}

function personaDocument(overrides = {}) {
  return {
    v: 1,
    platform: 'dsh',
    personas: [
      { name: 'pact-orchestrator', body: 'You are the PACT orchestrator.', description: 'x' },
    ],
    platform_notes: 'ToolSearch does not exist here.',
    ...overrides,
  };
}

test('the orchestrator persona becomes a system prompt section', () => {
  const sections = [];
  const ctx = fakeCtx({ systemPrompt: { section: (section) => sections.push(section) } });
  const playbook = writePlaybook(personaDocument());
  const text = registerPersona(ctx, { playbook }, ctx.logger);
  assert.equal(sections.length, 1);
  assert.equal(sections[0].name, 'pact:orchestrator');
  assert.equal(sections[0].order, 0);
  assert.match(sections[0].text, /PACT orchestrator/);
  assert.match(sections[0].text, /ToolSearch does not exist/);
  assert.equal(text, sections[0].text);
});

test('no playbook means no section and no failure', () => {
  const sections = [];
  const ctx = fakeCtx({ systemPrompt: { section: (section) => sections.push(section) } });
  assert.equal(registerPersona(ctx, {}, ctx.logger), null);
  assert.equal(sections.length, 0);
});

test('a missing system prompt service is reported, not thrown', () => {
  const ctx = fakeCtx({});
  const playbook = writePlaybook(personaDocument());
  assert.equal(registerPersona(ctx, { playbook }, ctx.logger), null);
  assert.match(ctx.warnings.join('\n'), /system prompt service unavailable/);
});

test('a malformed playbook is reported, not thrown', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'pact-playbook-bad-'));
  const target = path.join(directory, 'playbook.json');
  fs.writeFileSync(target, '{not json');
  const ctx = fakeCtx({ systemPrompt: { section: () => {} } });
  assert.equal(registerPersona(ctx, { playbook: target }, ctx.logger), null);
  assert.match(ctx.warnings.join('\n'), /cannot read the playbook/);
});

test('a playbook without an orchestrator is reported', () => {
  const ctx = fakeCtx({ systemPrompt: { section: () => {} } });
  const playbook = writePlaybook(personaDocument({ personas: [] }));
  assert.equal(registerPersona(ctx, { playbook }, ctx.logger), null);
  assert.match(ctx.warnings.join('\n'), /no orchestrator persona/);
});

test('loadPlaybook reads a document and rejects a non-object', () => {
  const good = writePlaybook(personaDocument());
  assert.equal(loadPlaybook(good).platform, 'dsh');
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'pact-playbook-arr-'));
  const target = path.join(directory, 'playbook.json');
  fs.writeFileSync(target, '[]');
  assert.throws(() => loadPlaybook(target), /not an object/);
});

test('the playbook path is part of the resolved config', () => {
  const config = normalizeConfig({ playbook: '/tmp/p.json' }, {});
  assert.equal(config.playbook, '/tmp/p.json');
});

function commandDocument() {
  return {
    v: 1,
    platform: 'dsh',
    personas: [],
    commands: [
      {
        name: 'pact-orchestrate',
        description: 'Delegate a task to PACT specialists',
        argument_hint: '[implement feature X]',
        body: 'Orchestrate the PACT cycle to address: {arguments}',
        source: 'commands/orchestrate.md',
      },
    ],
  };
}

test('commands are registered with the platform name and hint', () => {
  const registered = [];
  const ctx = fakeCtx({ commands: { register: (definition) => registered.push(definition) } });
  const playbook = writePlaybook(commandDocument());
  const count = registerCommands(ctx, { playbook }, { makeMessage: (text) => ({ text }) }, ctx.logger);
  assert.equal(count, 1);
  assert.equal(registered[0].name, 'pact-orchestrate');
  assert.equal(registered[0].input.hint, '[implement feature X]');
});

test('a command handler injects the body with the user text substituted', async () => {
  const registered = [];
  const ctx = fakeCtx({ commands: { register: (definition) => registered.push(definition) } });
  const playbook = writePlaybook(commandDocument());
  const delivered = [];
  const deps = { makeMessage: (text) => ({ text }) };
  registerCommands(ctx, { playbook }, deps, ctx.logger);
  const result = await registered[0].handler({
    rawInput: 'build authentication',
    agent: { followup: (message) => delivered.push(message) },
  });
  assert.equal(result.kind, 'success');
  assert.equal(delivered.length, 1);
  assert.match(delivered[0].text, /build authentication/);
  assert.ok(!delivered[0].text.includes('{arguments}'));
});

test('a missing command service is a warning, not a failure', () => {
  const ctx = fakeCtx({});
  const playbook = writePlaybook(commandDocument());
  assert.equal(registerCommands(ctx, { playbook }, { makeMessage: () => ({}) }, ctx.logger), 0);
  assert.match(ctx.warnings.join('\n'), /command service unavailable/);
});
