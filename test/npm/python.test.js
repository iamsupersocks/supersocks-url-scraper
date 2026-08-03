'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const { parseVersion, versionAtLeast, resolvePython, MIN_PYTHON } = require('../../lib/python');

test('parseVersion extracts major.minor.patch', () => {
  assert.deepEqual(parseVersion('Python 3.11.15'), { major: 3, minor: 11, patch: 15 });
  assert.deepEqual(parseVersion('3.10'), { major: 3, minor: 10, patch: 0 });
});

test('versionAtLeast accepts 3.10+', () => {
  assert.equal(versionAtLeast({ major: 3, minor: 10, patch: 0 }, MIN_PYTHON), true);
  assert.equal(versionAtLeast({ major: 3, minor: 9, patch: 9 }, MIN_PYTHON), false);
  assert.equal(versionAtLeast({ major: 4, minor: 0, patch: 0 }, MIN_PYTHON), true);
});

test('resolvePython returns first valid candidate', () => {
  const calls = [];
  const spawnSyncImpl = (command, args) => {
    calls.push([command, args.join(' ')]);
    if (command === 'python3') {
      return { status: 0, stdout: '3.11.2\n', stderr: '', error: undefined };
    }
    return { status: 1, stdout: '', stderr: 'missing', error: undefined };
  };
  const resolved = resolvePython({
    env: {},
    candidates: ['missing', 'python3'],
    spawnSyncImpl,
  });
  assert.equal(resolved.command, 'python3');
  assert.deepEqual(resolved.version, { major: 3, minor: 11, patch: 2 });
  assert.ok(calls.length >= 1);
});

test('resolvePython prefers PYTHON env override', () => {
  const spawnSyncImpl = (command) => {
    if (command === '/custom/python') {
      return { status: 0, stdout: '3.12.0\n', stderr: '', error: undefined };
    }
    return { status: 1, stdout: '', stderr: '', error: undefined };
  };
  const resolved = resolvePython({
    env: { PYTHON: '/custom/python' },
    spawnSyncImpl,
  });
  assert.equal(resolved.command, '/custom/python');
});

test('resolvePython rejects too-old interpreters with actionable error', () => {
  const spawnSyncImpl = (command) => {
    if (command === 'python3') {
      return { status: 0, stdout: '3.9.18\n', stderr: '', error: undefined };
    }
    return { status: 1, stdout: '', stderr: '', error: undefined };
  };
  assert.throws(
    () => resolvePython({ env: {}, candidates: ['python3'], spawnSyncImpl }),
    /Python >=3\.10 required; found too-old/,
  );
});

test('resolvePython errors when no interpreter exists', () => {
  const spawnSyncImpl = () => ({ status: 1, stdout: '', stderr: '', error: undefined });
  assert.throws(
    () => resolvePython({ env: {}, candidates: ['python3', 'python'], spawnSyncImpl }),
    /Python >=3\.10 not found/,
  );
});
