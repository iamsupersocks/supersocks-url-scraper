'use strict';

const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

const PACKAGE_NAME = 'supersocks-url-scraper';

function readPackageVersion(packageRoot) {
  const pkgPath = path.join(packageRoot, 'package.json');
  const raw = fs.readFileSync(pkgPath, 'utf8');
  const parsed = JSON.parse(raw);
  if (!parsed.version) {
    throw new Error('package.json is missing version');
  }
  return String(parsed.version);
}

function resolveCacheRoot(env = process.env, homedir = os.homedir) {
  if (env.SUPERSOCKS_URL_SCRAPER_CACHE && String(env.SUPERSOCKS_URL_SCRAPER_CACHE).trim()) {
    return path.resolve(String(env.SUPERSOCKS_URL_SCRAPER_CACHE).trim());
  }
  if (env.XDG_CACHE_HOME && String(env.XDG_CACHE_HOME).trim()) {
    return path.join(path.resolve(String(env.XDG_CACHE_HOME).trim()), PACKAGE_NAME);
  }
  const home = typeof homedir === 'function' ? homedir() : homedir;
  return path.join(home, '.cache', PACKAGE_NAME);
}

function versionPaths(cacheRoot, version) {
  const versionRoot = path.join(cacheRoot, `v${version}`);
  return {
    versionRoot,
    venvDir: path.join(versionRoot, 'venv'),
    readyMarker: path.join(versionRoot, '.ready'),
    lockDir: path.join(cacheRoot, `.lock-v${version}`),
    stagingDir: path.join(cacheRoot, `.staging-v${version}-${process.pid}`),
  };
}

function sleep(ms) {
  spawnSync(process.execPath, ['-e', `setTimeout(() => {}, ${Number(ms) || 0})`], {
    timeout: (Number(ms) || 0) + 2000,
    stdio: 'ignore',
  });
}

const LOCK_OWNER_FILE = 'owner.json';

function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) {
    return false;
  }
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function isLockStale(lockDir, staleMs, now, {
  statImpl = fs.statSync,
  readFileImpl = fs.readFileSync,
  isProcessAliveImpl = isProcessAlive,
} = {}) {
  const ownerPath = path.join(lockDir, LOCK_OWNER_FILE);
  try {
    const owner = JSON.parse(readFileImpl(ownerPath, 'utf8'));
    if (owner.pid && !isProcessAliveImpl(owner.pid)) {
      return true;
    }
    if (owner.timestamp && now() - owner.timestamp > staleMs) {
      return true;
    }
  } catch {
    // fall through to mtime
  }
  try {
    return now() - statImpl(lockDir).mtimeMs > staleMs;
  } catch {
    return false;
  }
}

function acquireLock(lockDir, {
  mkdirImpl = fs.mkdirSync,
  rmImpl = fs.rmSync,
  writeFileImpl = fs.writeFileSync,
  statImpl = fs.statSync,
  readFileImpl = fs.readFileSync,
  now = Date.now,
  pid = process.pid,
  timeoutMs = 120_000,
  staleMs = 300_000,
  sleepImpl = sleep,
  isProcessAliveImpl = isProcessAlive,
} = {}) {
  const started = now();
  while (true) {
    try {
      mkdirImpl(lockDir, { recursive: false });
      writeFileImpl(
        path.join(lockDir, LOCK_OWNER_FILE),
        JSON.stringify({ pid, timestamp: now() }),
        'utf8',
      );
      return () => {
        try {
          rmImpl(lockDir, { recursive: true, force: true });
        } catch {
          // ignore unlock failures
        }
      };
    } catch (err) {
      if (!err || err.code !== 'EEXIST') {
        throw err;
      }
      if (isLockStale(lockDir, staleMs, now, { statImpl, readFileImpl, isProcessAliveImpl })) {
        try {
          rmImpl(lockDir, { recursive: true, force: true });
          continue;
        } catch {
          // fall through and keep waiting
        }
      }
      if (now() - started > timeoutMs) {
        throw new Error(
          `Timed out waiting for install lock at ${lockDir}. Another install may be stuck; remove the lock directory and retry.`,
        );
      }
      sleepImpl(100);
    }
  }
}

module.exports = {
  PACKAGE_NAME,
  LOCK_OWNER_FILE,
  readPackageVersion,
  resolveCacheRoot,
  versionPaths,
  isProcessAlive,
  isLockStale,
  acquireLock,
};
