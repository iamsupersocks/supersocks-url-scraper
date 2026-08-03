'use strict';

const { spawn } = require('node:child_process');
const fs = require('node:fs');
const { fail } = require('./errors');
const { venvCliPath, venvPythonPath } = require('./bootstrap');

const FORWARD_SIGNALS = ['SIGINT', 'SIGTERM', 'SIGHUP'];

function resolveExecutable(runtime, { existsImpl = fs.existsSync } = {}) {
  // Prefer python -m so we never depend on a stale shebang after atomic venv rename.
  if (runtime.python && existsImpl(runtime.python)) {
    return {
      command: runtime.python,
      argsPrefix: ['-m', 'supersocks_url_scraper.cli'],
    };
  }
  const python = runtime.venvDir ? venvPythonPath(runtime.venvDir) : '';
  if (python && existsImpl(python)) {
    return { command: python, argsPrefix: ['-m', 'supersocks_url_scraper.cli'] };
  }
  if (runtime.cli && existsImpl(runtime.cli)) {
    return { command: runtime.cli, argsPrefix: [] };
  }
  const cli = runtime.venvDir ? venvCliPath(runtime.venvDir) : '';
  if (cli && existsImpl(cli)) {
    return { command: cli, argsPrefix: [] };
  }
  fail(`Installed runtime is incomplete under ${runtime.venvDir || '(unknown)'}. Delete the cache and retry.`);
}

function runPythonCli(runtime, argv, {
  spawnImpl = spawn,
  existsImpl = fs.existsSync,
  stdin = process.stdin,
  stdout = process.stdout,
  stderr = process.stderr,
  onSignal,
  exitProcess = (code) => {
    process.exit(code);
  },
} = {}) {
  const { command, argsPrefix } = resolveExecutable(runtime, { existsImpl });
  const child = spawnImpl(command, [...argsPrefix, ...argv], {
    stdio: [stdin, stdout, stderr],
    env: process.env,
  });

  const forward = (signal) => {
    if (!child.killed) {
      try {
        child.kill(signal);
      } catch {
        // ignore
      }
    }
  };

  const signalHandlers = [];
  for (const signal of FORWARD_SIGNALS) {
    const handler = () => {
      if (typeof onSignal === 'function') {
        onSignal(signal);
      }
      forward(signal);
    };
    signalHandlers.push([signal, handler]);
    if (typeof process.on === 'function') {
      process.on(signal, handler);
    }
  }

  const cleanup = () => {
    for (const [signal, handler] of signalHandlers) {
      if (typeof process.off === 'function') {
        process.off(signal, handler);
      } else if (typeof process.removeListener === 'function') {
        process.removeListener(signal, handler);
      }
    }
  };

  child.on('error', (err) => {
    cleanup();
    fail(`Failed to start Python CLI (${command}): ${err.message}`);
  });

  child.on('exit', (code, signal) => {
    cleanup();
    if (signal) {
      // Best-effort: re-raise the same signal to the launcher process.
      try {
        process.kill(process.pid, signal);
      } catch {
        exitProcess(1);
      }
      return;
    }
    exitProcess(code == null ? 1 : code);
  });

  return child;
}

module.exports = {
  FORWARD_SIGNALS,
  resolveExecutable,
  runPythonCli,
};
