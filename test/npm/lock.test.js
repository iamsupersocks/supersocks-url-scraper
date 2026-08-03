'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {
  LOCK_OWNER_FILE,
  acquireLock,
  isLockStale,
} = require('../../lib/cache');

function makeTempLockDir() {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'sus-lock-'));
  return path.join(root, '.lock-vtest');
}

test('acquireLock creates owner metadata and release removes lock dir', () => {
  const lockDir = makeTempLockDir();
  const root = path.dirname(lockDir);
  let nowMs = 1_000_000;
  try {
    const release = acquireLock(lockDir, {
      now: () => nowMs,
      pid: 4242,
    });
    assert.ok(fs.existsSync(lockDir));
    const owner = JSON.parse(fs.readFileSync(path.join(lockDir, LOCK_OWNER_FILE), 'utf8'));
    assert.equal(owner.pid, 4242);
    assert.equal(owner.timestamp, nowMs);

    release();
    assert.equal(fs.existsSync(lockDir), false);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('acquireLock waits on a fresh lock and times out without removing it', () => {
  const lockDir = makeTempLockDir();
  const root = path.dirname(lockDir);
  let nowMs = 5_000;
  const sleeps = [];
  try {
    fs.mkdirSync(lockDir, { recursive: false });
    fs.writeFileSync(
      path.join(lockDir, LOCK_OWNER_FILE),
      JSON.stringify({ pid: 999_999, timestamp: nowMs }),
      'utf8',
    );
    fs.utimesSync(lockDir, nowMs / 1000, nowMs / 1000);

    assert.throws(
      () => acquireLock(lockDir, {
        now: () => {
          nowMs += 50;
          return nowMs;
        },
        timeoutMs: 200,
        staleMs: 10_000,
        sleepImpl: (ms) => sleeps.push(ms),
        isProcessAliveImpl: () => true,
      }),
      /Timed out waiting for install lock/,
    );

    assert.ok(fs.existsSync(lockDir));
    assert.ok(sleeps.length >= 1);
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('acquireLock reclaims a stale lock from dead owner metadata', () => {
  const lockDir = makeTempLockDir();
  const root = path.dirname(lockDir);
  let nowMs = 100_000;
  try {
    fs.mkdirSync(lockDir, { recursive: false });
    fs.writeFileSync(
      path.join(lockDir, LOCK_OWNER_FILE),
      JSON.stringify({ pid: 777, timestamp: nowMs - 60_000 }),
      'utf8',
    );

    const release = acquireLock(lockDir, {
      now: () => nowMs,
      pid: 888,
      staleMs: 1_000,
      timeoutMs: 500,
      sleepImpl: () => {},
      isProcessAliveImpl: () => false,
    });

    const owner = JSON.parse(fs.readFileSync(path.join(lockDir, LOCK_OWNER_FILE), 'utf8'));
    assert.equal(owner.pid, 888);
    release();
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('acquireLock reclaims a stale lock from old directory mtime', () => {
  const lockDir = makeTempLockDir();
  const root = path.dirname(lockDir);
  let nowMs = 200_000;
  try {
    fs.mkdirSync(lockDir, { recursive: false });
    fs.utimesSync(lockDir, (nowMs - 60_000) / 1000, (nowMs - 60_000) / 1000);

    const release = acquireLock(lockDir, {
      now: () => nowMs,
      pid: 1234,
      staleMs: 1_000,
      timeoutMs: 500,
      sleepImpl: () => {},
      isProcessAliveImpl: () => true,
    });

    assert.ok(fs.existsSync(path.join(lockDir, LOCK_OWNER_FILE)));
    release();
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});

test('isLockStale returns false for a fresh lock', () => {
  const lockDir = makeTempLockDir();
  const root = path.dirname(lockDir);
  const nowMs = 300_000;
  try {
    fs.mkdirSync(lockDir, { recursive: false });
    fs.writeFileSync(
      path.join(lockDir, LOCK_OWNER_FILE),
      JSON.stringify({ pid: 1, timestamp: nowMs - 100 }),
      'utf8',
    );

    assert.equal(
      isLockStale(lockDir, 1_000, () => nowMs, {
        statImpl: () => ({ mtimeMs: nowMs - 100 }),
        readFileImpl: fs.readFileSync,
        isProcessAliveImpl: () => true,
      }),
      false,
    );
  } finally {
    fs.rmSync(root, { recursive: true, force: true });
  }
});
