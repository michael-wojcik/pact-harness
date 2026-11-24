/**
 * The bridge's client for the warm harness worker.
 *
 * One worker serves the whole session: it imports PACT's shared tree once and
 * forks a child per hook, so a tool call pays a socket round trip and a fork
 * instead of a fresh interpreter start per hook. The bridge starts it lazily
 * and stops it when the plugin is disposed.
 */

import { spawn } from 'node:child_process';
import { connect } from 'node:net';
import { mkdirSync } from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const START_TIMEOUT_MS = 20000;
const DEFAULT_TIMEOUT_MS = 60000;

let socketCounter = 0;

/** A per-process socket path short enough for a unix socket. */
export function socketPathFor(stateRoot, pid = process.pid) {
  socketCounter += 1;
  const stem = `dsh-${pid}-${socketCounter}.sock`;
  if (stateRoot) return path.join(String(stateRoot), 'pact-harness', stem);
  return path.join(os.tmpdir(), 'pact-harness', stem);
}

/** Send one JSON request over a unix socket and read one JSON response. */
export function requestOverSocket(socketPath, payload, timeoutMs = DEFAULT_TIMEOUT_MS) {
  return new Promise((resolve, reject) => {
    const client = connect(socketPath);
    let buffer = '';
    let settled = false;
    const finish = (error, value) => {
      if (settled) return;
      settled = true;
      client.destroy();
      if (error) reject(error);
      else resolve(value);
    };
    client.setEncoding('utf8');
    client.setTimeout(timeoutMs);
    client.on('connect', () => client.write(JSON.stringify(payload) + '\n'));
    client.on('data', (chunk) => {
      buffer += chunk;
      const newline = buffer.indexOf('\n');
      if (newline === -1) return;
      try {
        finish(null, JSON.parse(buffer.slice(0, newline)));
      } catch (error) {
        finish(new Error(`unreadable worker response: ${error.message}`));
      }
    });
    client.on('timeout', () => finish(new Error('worker request timed out')));
    client.on('error', (error) => finish(error));
    client.on('close', () => {
      if (!settled) finish(new Error('worker closed the connection without a response'));
    });
  });
}

export class HarnessWorker {
  constructor(options = {}) {
    this.python = options.python ?? 'python3';
    this.pluginRoot = options.pluginRoot;
    this.stateRoot = options.stateRoot;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.env = options.env ?? process.env;
    this.logger = options.logger;
    this.socketPath = options.socketPath ?? socketPathFor(this.stateRoot);
    this.child = null;
    this.starting = null;
  }

  /** Start the worker, or return the in-flight start. */
  start() {
    if (this.starting) return this.starting;
    this.starting = this.#start();
    return this.starting;
  }

  async #start() {
    mkdirSync(path.dirname(this.socketPath), { recursive: true });
    const args = ['-m', 'pact_harness', 'serve', '--socket', this.socketPath];
    if (this.pluginRoot) args.push('--plugin-root', String(this.pluginRoot));
    const child = spawn(this.python, args, {
      env: { ...this.env },
      stdio: ['ignore', 'ignore', 'pipe'],
    });
    this.child = child;
    let stderr = '';
    child.stderr?.setEncoding?.('utf8');
    child.stderr?.on?.('data', (chunk) => {
      stderr += String(chunk);
    });
    let exited = false;
    child.once('exit', (code) => {
      exited = true;
      if (code !== 0 && this.logger?.warn) {
        this.logger.warn(`pact-dsh: worker exited with ${code}: ${stderr.trim()}`);
      }
    });

    const deadline = Date.now() + START_TIMEOUT_MS;
    while (Date.now() < deadline) {
      if (exited) throw new Error(`worker exited during startup: ${stderr.trim()}`);
      try {
        await requestOverSocket(this.socketPath, { type: 'ping' }, 500);
        return this;
      } catch {
        await new Promise((resolve) => setTimeout(resolve, 50));
      }
    }
    this.stop();
    throw new Error('worker did not accept a connection in time');
  }

  /** Evaluate one normalised event. */
  async evaluate(event) {
    await this.start();
    const response = await requestOverSocket(
      this.socketPath,
      {
        type: 'evaluate',
        event,
        state_root: this.stateRoot,
        timeout_s: Math.max(1, Math.floor(this.timeoutMs / 1000)),
      },
      this.timeoutMs,
    );
    if (response?.status === 'error') {
      throw new Error(response.error ?? 'worker reported an evaluation error');
    }
    if (!response?.envelope) throw new Error('worker returned no envelope');
    return response.envelope;
  }

  /** Stop the worker. */
  stop() {
    this.starting = null;
    if (this.child && this.child.exitCode === null) {
      this.child.kill();
    }
    this.child = null;
  }
}
