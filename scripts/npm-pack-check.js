'use strict';

/**
 * Validate that `npm pack` would only include the intended distribution files.
 * Usage: node scripts/npm-pack-check.js
 */

const { spawnSync } = require('node:child_process');
const path = require('node:path');

const root = path.resolve(__dirname, '..');

const REQUIRED_PREFIXES = [
  'package.json',
  'bin/',
  'lib/',
  'src/',
  'pyproject.toml',
  'README.md',
  'LICENSE',
];

const FORBIDDEN_PATTERNS = [
  /^tests\//,
  /^test\//,
  /^scripts\//,
  /^docs\//,
  /^examples\//,
  /^Dockerfile$/,
  /^docker-compose\.yml$/,
  /^\.env/,
  /^\.git/,
  /^\.worktrees\//,
  /^dist\//,
  /^build\//,
  /^\.venv\//,
  /^node_modules\//,
  /^__pycache__\//,
  /\.pyc$/,
  /\.tgz$/,
  /^runs\//,
  /^browser-profiles\//,
  /^data\//,
];

function main() {
  const result = spawnSync('npm', ['pack', '--dry-run', '--json'], {
    cwd: root,
    encoding: 'utf8',
    env: process.env,
  });
  if (result.status !== 0) {
    process.stderr.write(result.stderr || result.stdout || 'npm pack --dry-run failed\n');
    process.exit(result.status || 1);
  }

  let parsed;
  try {
    parsed = JSON.parse(result.stdout);
  } catch (err) {
    process.stderr.write(`Failed to parse npm pack JSON: ${err.message}\n`);
    process.exit(1);
  }

  const entry = Array.isArray(parsed) ? parsed[0] : parsed;
  const files = (entry && entry.files) || [];
  const names = files.map((f) => f.path || f).filter(Boolean);

  const missing = [];
  for (const required of REQUIRED_PREFIXES) {
    const found = names.some((name) => name === required || name.startsWith(required));
    if (!found) {
      missing.push(required);
    }
  }

  const forbidden = names.filter((name) => FORBIDDEN_PATTERNS.some((re) => re.test(name)));

  if (missing.length || forbidden.length) {
    if (missing.length) {
      process.stderr.write(`pack:check missing required entries: ${missing.join(', ')}\n`);
    }
    if (forbidden.length) {
      process.stderr.write(`pack:check forbidden entries present:\n  ${forbidden.join('\n  ')}\n`);
    }
    process.exit(1);
  }

  process.stdout.write(`pack:check ok (${names.length} files)\n`);
}

main();
