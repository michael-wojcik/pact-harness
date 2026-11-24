import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import {
  findPersona,
  registerPactAgent,
  loadPlaybook,
  normalizeConfig,
  registerCommands,
  registerPersona,
  registerSkills,
  stripFrontmatter,
  wire,
} from '../index.js';
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

function agentServices() {
  const registered = [];
  const activations = [];
  const tools = { register: (definition) => registered.push(definition) };
  const subagents = {
    startActivation: async (spec) => {
      activations.push(spec);
      return { name: spec.name, childId: 'child-1' };
    },
  };
  const defineTool = (options) => options;
  return { registered, activations, tools, subagents, defineTool };
}

test('pact_agent turns a persona name into a child label', async () => {
  const services = agentServices();
  const ctx = fakeCtx({ tools: services.tools, subagents: services.subagents });
  const count = await registerPactAgent(ctx, {}, ctx.logger, { defineTool: services.defineTool });
  assert.equal(count, 1);
  assert.equal(services.registered[0].name, 'pact_agent');
  const value = await services.registered[0].execute(
    { subagent_type: 'pact-architect', prompt: 'design the data model' },
    { agent: { id: 'lead' }, signal: 'sig' },
  );
  assert.equal(services.activations.length, 1);
  const spec = services.activations[0];
  assert.equal(spec.provider, 'spawn');
  assert.equal(spec.label, 'architect');
  assert.equal(spec.name, 'architect');
  assert.equal(spec.delivery, 'parent');
  assert.equal(spec.prompt[0].text, 'design the data model');
  assert.equal(value.name, 'architect');
  assert.equal(value.child_id, 'child-1');
});

test('pact_agent honours an explicit short name and a configured provider', async () => {
  const services = agentServices();
  const ctx = fakeCtx({ tools: services.tools, subagents: services.subagents });
  await registerPactAgent(ctx, { subagentProvider: 'fork' }, ctx.logger, {
    defineTool: services.defineTool,
  });
  await services.registered[0].execute(
    { subagent_type: 'pact-qa-engineer', name: 'qa', prompt: 'test it' },
    { agent: {}, signal: 's' },
  );
  assert.equal(services.activations[0].provider, 'fork');
  assert.equal(services.activations[0].name, 'qa');
});

test('pact_agent refuses without a calling agent', async () => {
  const services = agentServices();
  const ctx = fakeCtx({ tools: services.tools, subagents: services.subagents });
  await registerPactAgent(ctx, {}, ctx.logger, { defineTool: services.defineTool });
  await assert.rejects(
    services.registered[0].execute({ subagent_type: 'pact-architect', prompt: 'x' }, {}),
    /no calling agent/,
  );
});

test('pact_agent is skipped when a service is absent', async () => {
  const ctx = fakeCtx({ tools: undefined, subagents: undefined });
  assert.equal(await registerPactAgent(ctx, {}, ctx.logger, {}), 0);
});

test('pact_agent degrades when the tool DSL cannot load', async () => {
  const services = agentServices();
  const ctx = fakeCtx({ tools: services.tools, subagents: services.subagents });
  // No injected defineTool and no @deepseek-ai/dsh-tools in this test process.
  assert.equal(await registerPactAgent(ctx, {}, ctx.logger, {}), 0);
  assert.match(ctx.warnings.join('\n'), /tool DSL unavailable/);
});

test('the configured subagent provider defaults to spawn', () => {
  assert.equal(normalizeConfig({}, {}).subagentProvider, 'spawn');
  assert.equal(normalizeConfig({ subagentProvider: 'fork' }, {}).subagentProvider, 'fork');
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

test('frontmatter is stripped from a skill body', () => {
  assert.equal(stripFrontmatter('---\nname: x\n---\n\nBody\n'), '\nBody\n');
  assert.equal(stripFrontmatter('no frontmatter'), 'no frontmatter');
  assert.equal(stripFrontmatter('---\nunclosed'), '---\nunclosed');
});

test('personas match on the bare and prefixed spelling', () => {
  const playbook = { personas: [{ name: 'pact-architect', body: 'design' }] };
  assert.equal(findPersona(playbook, 'architect').name, 'pact-architect');
  assert.equal(findPersona(playbook, 'pact-architect').name, 'pact-architect');
  assert.equal(findPersona(playbook, 'unknown'), undefined);
  assert.equal(findPersona({}, 'architect'), undefined);
});

function skillDocument(directory) {
  return {
    v: 1,
    platform: 'dsh',
    personas: [],
    commands: [],
    skills: [
      {
        name: 'pact-memory',
        description: 'Persistent memory for PACT agents.',
        path: directory,
        source: 'skills/pact-memory/SKILL.md',
      },
    ],
  };
}

test('skills are registered as a zero-copy provider', async () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'pact-skill-'));
  fs.writeFileSync(
    path.join(directory, 'SKILL.md'),
    '---\nname: pact-memory\ndescription: memory\n---\n\n# Memory\nBody here.\n',
  );
  const providers = [];
  const ctx = fakeCtx({ skills: { registerProvider: (factory) => providers.push(factory()) } });
  const playbook = writePlaybook(skillDocument(directory));
  const count = registerSkills(ctx, { playbook }, ctx.logger);
  assert.equal(count, 1);
  assert.equal(providers.length, 1);
  assert.equal(providers[0].name, 'pact');

  const candidates = await providers[0].list({});
  assert.equal(candidates.length, 1);
  assert.equal(candidates[0].name, 'pact-memory');
  assert.equal(candidates[0].rank, 300);
  assert.equal(candidates[0].provider, 'pact');
  assert.equal(candidates[0].resourceBase.kind, 'directory');

  const definition = await providers[0].get(candidates[0]);
  assert.match(definition.content, /# Memory/);
  assert.ok(!definition.content.includes('name: pact-memory'));
  assert.equal(definition.provider, 'pact');
});

test('a missing skill service is a warning, not a failure', () => {
  const ctx = fakeCtx({});
  const playbook = writePlaybook(skillDocument('/tmp/x'));
  assert.equal(registerSkills(ctx, { playbook }, ctx.logger), 0);
  assert.match(ctx.warnings.join('\n'), /skill service unavailable/);
});

test('a teammate agent receives its specialist persona on creation', async () => {
  const ctx = fakeCtx({});
  const injected = [];
  const deps = depsFor(async () => envelope([]), ctx);
  const playbook = writePlaybook({
    v: 1,
    platform: 'dsh',
    personas: [{ name: 'pact-architect', body: 'You design systems.' }],
    commands: [],
    skills: [],
  });
  deps.playbook = loadPlaybook(playbook);
  wire(ctx, settings(), deps);
  await ctx.emit('agent/created', {
    agent: {
      session: { id: 's', header: { cwd: '/tmp' } },
      name: 'architect',
      parentSession: 'parent-1',
      inject: (message) => injected.push(message),
    },
    source: 'startup',
  });
  const texts = injected.map((message) => message.content[0].text);
  assert.ok(texts.some((text) => text.includes('PACT ROLE (pact-architect)')));
  assert.ok(texts.some((text) => text.includes('You design systems.')));
});

test('the lead agent does not receive a specialist persona', async () => {
  const ctx = fakeCtx({});
  const injected = [];
  const deps = depsFor(async () => envelope([]), ctx);
  deps.playbook = { personas: [{ name: 'pact-architect', body: 'You design systems.' }] };
  wire(ctx, settings(), deps);
  await ctx.emit('agent/created', {
    agent: {
      session: { id: 's', header: { cwd: '/tmp' } },
      name: 'architect',
      inject: (message) => injected.push(message),
    },
    source: 'startup',
  });
  assert.equal(injected.length, 0);
});
