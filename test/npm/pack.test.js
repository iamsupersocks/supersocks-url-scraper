'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawn, spawnSync } = require('node:child_process');

const ROOT = path.resolve(__dirname, '../..');

function listTarballNames(tgzPath) {
  const listed = spawnSync('tar', ['-tzf', tgzPath], { encoding: 'utf8' });
  assert.equal(listed.status, 0, listed.stderr);
  return listed.stdout
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.replace(/^package\//, ''));
}

function startFixtureServer(html) {
  const script = `
const http = require('http');
const html = ${JSON.stringify(html)};
const server = http.createServer((req, res) => {
  res.writeHead(200, { 'content-type': 'text/html; charset=utf-8' });
  res.end(html);
});
server.listen(0, '127.0.0.1', () => {
  process.stdout.write(String(server.address().port) + '\\n');
});
`;
  const child = spawn(process.execPath, ['-e', script], {
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  return new Promise((resolve, reject) => {
    let settled = false;
    let buffer = '';
    const timer = setTimeout(() => {
      if (!settled) {
        settled = true;
        child.kill('SIGTERM');
        reject(new Error('fixture server timed out'));
      }
    }, 5000);
    child.stderr.on('data', () => {});
    child.stdout.on('data', (chunk) => {
      buffer += String(chunk);
      const line = buffer.split(/\r?\n/).find((part) => /^\d+$/.test(part.trim()));
      if (!line || settled) {
        return;
      }
      settled = true;
      clearTimeout(timer);
      const port = Number(line.trim());
      resolve({
        url: `http://127.0.0.1:${port}/article`,
        close: () => {
          child.kill('SIGTERM');
        },
      });
    });
    child.on('error', (err) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(err);
      }
    });
    child.on('exit', (code) => {
      if (!settled) {
        settled = true;
        clearTimeout(timer);
        reject(new Error(`fixture server exited early (${code})`));
      }
    });
  });
}

test('npm pack tarball contains only allowed distribution files', () => {
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sus-pack-'));
  try {
    const packed = spawnSync('npm', ['pack', '--pack-destination', tmp, '--json'], {
      cwd: ROOT,
      encoding: 'utf8',
      env: process.env,
    });
    assert.equal(packed.status, 0, packed.stderr || packed.stdout);
    const json = JSON.parse(packed.stdout);
    const entry = Array.isArray(json) ? json[0] : json;
    const filename = entry.filename || 'supersocks-url-scraper-0.2.0.tgz';
    const tgzPath = path.join(tmp, path.basename(filename));
    assert.ok(fs.existsSync(tgzPath), `missing tarball at ${tgzPath}`);

    const names = listTarballNames(tgzPath);
    assert.ok(names.includes('package.json'));
    assert.ok(names.includes('pyproject.toml'));
    assert.ok(names.includes('bin/supersocks-url-scraper.js'));
    assert.ok(names.some((n) => n.startsWith('lib/')));
    assert.ok(names.some((n) => n.startsWith('src/supersocks_url_scraper/')));
    assert.ok(names.includes('LICENSE'));
    assert.ok(names.includes('README.md'));

    const forbidden = names.filter((name) =>
      /^(tests\/|test\/|scripts\/|docs\/|examples\/|dist\/|build\/|\.venv\/|node_modules\/|runs\/)/.test(name)
      || name === 'Dockerfile'
      || name === 'docker-compose.yml'
      || name.startsWith('.env'),
    );
    assert.deepEqual(forbidden, []);
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});

test('smoke: npm pack install in temp HOME runs against local fixture without touching real HOME', async () => {
  const realHome = os.homedir();
  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'sus-smoke-'));
  const home = path.join(tmp, 'home');
  const xdg = path.join(home, '.cache');
  const prefix = path.join(tmp, 'npm-prefix');
  fs.mkdirSync(home, { recursive: true });
  fs.mkdirSync(prefix, { recursive: true });

  const fixtureHtml =
    '<!doctype html><html><head><title>Smoke Fixture</title></head><body><article><h1>Smoke Fixture</h1><p>Hello from local fixture for npm pack smoke.</p></article></body></html>\n';
  const fixture = await startFixtureServer(fixtureHtml);

  try {
    const packed = spawnSync('npm', ['pack', '--pack-destination', tmp, '--json'], {
      cwd: ROOT,
      encoding: 'utf8',
      env: process.env,
    });
    assert.equal(packed.status, 0, packed.stderr || packed.stdout);
    const json = JSON.parse(packed.stdout);
    const entry = Array.isArray(json) ? json[0] : json;
    const tgzName = entry.filename || 'supersocks-url-scraper-0.2.0.tgz';
    const tgzPath = path.join(tmp, path.basename(tgzName));
    assert.ok(fs.existsSync(tgzPath), tgzPath);

    const install = spawnSync(
      'npm',
      ['install', '-g', tgzPath, '--prefix', prefix],
      {
        encoding: 'utf8',
        env: {
          ...process.env,
          HOME: home,
          npm_config_prefix: prefix,
        },
      },
    );
    assert.equal(install.status, 0, install.stderr || install.stdout);

    const bin = path.join(prefix, 'bin', 'supersocks-url-scraper');
    assert.ok(fs.existsSync(bin), `missing bin at ${bin}`);

    const run = spawnSync(bin, ['--no-seo-fallback', '--no-archive-fallback', '--length', '200', fixture.url], {
      encoding: 'utf8',
      env: {
        ...process.env,
        HOME: home,
        XDG_CACHE_HOME: xdg,
      },
    });
    assert.equal(run.status, 0, run.stderr || run.stdout);
    assert.match(run.stdout, /Smoke Fixture|Hello from local fixture/i);

    const cacheRoot = path.join(xdg, 'supersocks-url-scraper');
    assert.ok(fs.existsSync(cacheRoot), 'expected versioned cache under temp XDG');
    assert.ok(fs.existsSync(path.join(cacheRoot, 'v0.2.0', '.ready')));

    assert.equal(os.homedir(), realHome, 'test must not change process home');
    assert.equal(
      fs.existsSync(path.join(realHome, '.cache', 'supersocks-url-scraper', 'v0.2.0', '.npm-pack-smoke-marker')),
      false,
    );
  } finally {
    fixture.close();
    fs.rmSync(tmp, { recursive: true, force: true });
  }
});
