import assert from 'node:assert/strict';
import { execSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';

import { normalizeConfig } from '../index.js';
import { HarnessWorker, socketPathFor } from '../lib/worker.js';

const REPO_ROOT = path.resolve(import.meta.dirname, '..', '..', '..');
const SRC = path.join(REPO_ROOT, 'python', 'src');
const PLUGIN = path.join(REPO_ROOT, 'vendor', 'pact-plugin', 'pact-plugin');

function pythonAvailable() {
  if (!fs.existsSync(PLUGIN)) return false;
  try {
    execSync(
      'python3 -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 12) else 1)"',
      { stdio: 'ignore' },
    );
    execSync('python3 -c "import pact_harness"', {
      stdio: 'ignore',
      env: { ...process.env, PYTHONPATH: SRC },
    });
    return true;
  } catch {
    return false;
  }
}

test('socket paths stay short and are per process', () => {
  const first = socketPathFor('/tmp/state', 1234);
  const second = socketPathFor(undefined, 1234);
  assert.match(first, /pact-harness\/dsh-1234-\d+\.sock$/);
  assert.ok(second.startsWith(os.tmpdir()));
  assert.ok(first.length < 100);
});

test('config resolves interpreter and roots from the environment', () => {
  const config = normalizeConfig({}, { PACT_DSH_PYTHON: '/opt/py', PACT_PLUGIN_ROOT: '/p', PACT_HOME: '/s' });
  assert.equal(config.python, '/opt/py');
  assert.equal(config.pluginRoot, '/p');
  assert.equal(config.stateRoot, '/s');
});

test('the state root defaults to the same place the Python side uses', () => {
  const config = normalizeConfig({}, {});
  assert.equal(config.stateRoot, path.join(os.homedir(), '.pact'));
});

test('the worker evaluates a DSH event end to end', { skip: !pythonAvailable() }, async (t) => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pact-dsh-test-'));
  const worker = new HarnessWorker({
    python: 'python3',
    pluginRoot: PLUGIN,
    stateRoot,
    env: { ...process.env, PYTHONPATH: SRC },
    timeoutMs: 60000,
  });
  t.after(() => worker.stop());

  const envelope = await worker.evaluate({
    v: 1,
    id: 'evt_node_1',
    kind: 'tool.pre',
    at: new Date().toISOString(),
    adapter: 'dsh',
    session: { id: 'sess-node', cwd: REPO_ROOT, project_dir: REPO_ROOT },
    actor: { role: 'lead', name: 'lead', type: 'pact-orchestrator' },
    tool: {
      canonical: 'Edit',
      native: 'edit',
      input: { file_path: path.join(REPO_ROOT, 'README.md') },
    },
  });

  assert.equal(envelope.v, 1);
  assert.equal(envelope.meta.adapter, 'dsh');
  assert.equal(envelope.meta.event, 'tool.pre');
  assert.ok(Array.isArray(envelope.directives));
  assert.ok(envelope.directives.length > 0);
  assert.ok(envelope.meta.elapsed_ms >= 0);
});

test('a real gate refuses a real force-push through the worker', { skip: !pythonAvailable() }, async (t) => {
  const stateRoot = fs.mkdtempSync(path.join(os.tmpdir(), 'pact-dsh-deny-'));
  const worker = new HarnessWorker({
    python: 'python3',
    pluginRoot: PLUGIN,
    stateRoot,
    env: { ...process.env, PYTHONPATH: SRC },
    timeoutMs: 60000,
  });
  t.after(() => worker.stop());

  const envelope = await worker.evaluate({
    v: 1,
    id: 'evt_node_deny',
    kind: 'tool.pre',
    at: new Date().toISOString(),
    adapter: 'dsh',
    session: { id: 'sess-deny', cwd: REPO_ROOT, project_dir: REPO_ROOT },
    actor: { role: 'lead', name: 'lead', type: 'pact-orchestrator' },
    tool: {
      canonical: 'Bash',
      native: 'bash',
      input: { command: 'git push --force origin main' },
    },
  });

  const refusal = envelope.directives.find((directive) => directive.kind === 'deny');
  assert.ok(refusal, 'merge_guard_pre should refuse a force-push');
  assert.equal(refusal.rule, 'merge_guard_pre');
  assert.match(refusal.reason, /approval/i);
});
