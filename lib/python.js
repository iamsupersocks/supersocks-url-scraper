'use strict';

const { spawnSync } = require('node:child_process');
const { fail } = require('./errors');

const MIN_PYTHON = { major: 3, minor: 10 };

function parseVersion(text) {
  const match = String(text || '').match(/(\d+)\.(\d+)(?:\.(\d+))?/);
  if (!match) {
    return null;
  }
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3] || 0),
  };
}

function versionAtLeast(version, minimum = MIN_PYTHON) {
  if (!version) {
    return false;
  }
  if (version.major !== minimum.major) {
    return version.major > minimum.major;
  }
  return version.minor >= minimum.minor;
}

function probePython(command, { spawnSyncImpl = spawnSync } = {}) {
  const result = spawnSyncImpl(command, ['-c', 'import sys; print("%d.%d.%d" % sys.version_info[:3])'], {
    encoding: 'utf8',
    env: process.env,
  });
  if (result.error || result.status !== 0) {
    return null;
  }
  const version = parseVersion((result.stdout || '').trim());
  if (!version) {
    return null;
  }
  return { command, version };
}

function defaultCandidates(env = process.env) {
  const fromEnv = [];
  if (env.PYTHON && String(env.PYTHON).trim()) {
    fromEnv.push(String(env.PYTHON).trim());
  }
  if (env.SUPERSOCKS_PYTHON && String(env.SUPERSOCKS_PYTHON).trim()) {
    fromEnv.push(String(env.SUPERSOCKS_PYTHON).trim());
  }
  return [...fromEnv, 'python3', 'python'];
}

function resolvePython({
  env = process.env,
  candidates,
  spawnSyncImpl = spawnSync,
  minimum = MIN_PYTHON,
} = {}) {
  const seen = new Set();
  const list = candidates || defaultCandidates(env);
  const rejected = [];

  for (const command of list) {
    if (!command || seen.has(command)) {
      continue;
    }
    seen.add(command);
    const probed = probePython(command, { spawnSyncImpl });
    if (!probed) {
      continue;
    }
    if (!versionAtLeast(probed.version, minimum)) {
      rejected.push(`${command} (${probed.version.major}.${probed.version.minor}.${probed.version.patch})`);
      continue;
    }
    return probed;
  }

  if (rejected.length) {
    fail(
      `Python >=${minimum.major}.${minimum.minor} required; found too-old interpreter(s): ${rejected.join(', ')}. Install Python ${minimum.major}.${minimum.minor}+ and retry.`,
    );
  }
  fail(
    `Python >=${minimum.major}.${minimum.minor} not found. Install python3 (3.10+) and ensure it is on PATH, or set PYTHON=/path/to/python3.`,
  );
}

module.exports = {
  MIN_PYTHON,
  parseVersion,
  versionAtLeast,
  probePython,
  resolvePython,
  defaultCandidates,
};
