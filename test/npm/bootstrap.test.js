'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { ensureRuntime } = require('../../lib/bootstrap');
const { runPythonCli } = require('../../lib/run');

const PACKAGE_ROOT = path.resolve(__dirname, '../..');

function makeTempHome() {
  return fs.mkdtempSync(path.join(os.tmpdir(), 'sus-npm-home-'));
}

test('bootstrap creates versioned cache under XDG and is idempotent', () => {
  const home = makeTempHome();
  const xdg = path.join(home, 'cache');
  try {
    const env = {
      ...process.env,
      HOME: home,
      XDG_CACHE_HOME: xdg,
    };
    delete env.SUPERSOCKS_URL_SCRAPER_CACHE;

    const first = ensureRuntime({
      packageRoot: PACKAGE_ROOT,
      env,
      homedir: () => home,
    });
    assert.equal(first.created, true);
    assert.equal(first.version, '0.2.0');
    assert.ok(first.cacheRoot.startsWith(xdg));
    assert.ok(fs.existsSync(first.python));
    assert.ok(fs.existsSync(first.readyMarker));
    const marker = fs.readFileSync(first.readyMarker, 'utf8');
    assert.match(marker, /^0\.2\.0\n/);

    const second = ensureRuntime({
      packageRoot: PACKAGE_ROOT,
      env,
      homedir: () => home,
    });
    assert.equal(second.created, false);
    assert.equal(second.venvDir, first.venvDir);
    assert.equal(fs.readFileSync(second.readyMarker, 'utf8'), marker);
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
});

test('runPythonCli forwards args and exit code via doubles', async () => {
  const calls = [];
  let exitCode = null;
  const fakeChild = {
    killed: false,
    kill() {
      this.killed = true;
    },
    on(event, handler) {
      if (event === 'exit') {
        setImmediate(() => handler(7, null));
      }
    },
  };
  const spawnImpl = (command, args, options) => {
    calls.push({ command, args, options });
    return fakeChild;
  };

  await new Promise((resolve, reject) => {
    try {
      runPythonCli(
        {
          cli: '/tmp/fake-cli',
          python: '/tmp/fake-python',
          venvDir: '/tmp/fake-venv',
        },
        ['--length', '10', 'https://example.com'],
        {
          spawnImpl,
          existsImpl: (p) => p === '/tmp/fake-cli',
          exitProcess: (code) => {
            exitCode = code;
            resolve();
          },
        },
      );
    } catch (err) {
      reject(err);
    }
  });

  assert.equal(exitCode, 7);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].command, '/tmp/fake-cli');
  assert.deepEqual(calls[0].args, ['--length', '10', 'https://example.com']);
});

test('real bootstrap can execute embedded CLI --help', () => {
  const home = makeTempHome();
  const xdg = path.join(home, 'cache');
  try {
    const env = {
      ...process.env,
      HOME: home,
      XDG_CACHE_HOME: xdg,
    };
    delete env.SUPERSOCKS_URL_SCRAPER_CACHE;
    const runtime = ensureRuntime({
      packageRoot: PACKAGE_ROOT,
      env,
      homedir: () => home,
    });
    const result = spawnSync(runtime.python, ['-m', 'supersocks_url_scraper.cli', '--help'], {
      encoding: 'utf8',
      env: process.env,
    });
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.match(result.stdout + result.stderr, /usage|supersocks|url/i);
    assert.ok(fs.existsSync(runtime.cli));
    const cliHelp = spawnSync(runtime.cli, ['--help'], { encoding: 'utf8', env: process.env });
    assert.equal(cliHelp.status, 0, cliHelp.stderr || cliHelp.stdout);
  } finally {
    fs.rmSync(home, { recursive: true, force: true });
  }
});
