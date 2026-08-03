#!/usr/bin/env node
'use strict';

const path = require('node:path');
const { formatError } = require('../lib/errors');
const { ensureRuntime, packageRootFrom } = require('../lib/bootstrap');
const { runPythonCli } = require('../lib/run');

function main(argv = process.argv.slice(2)) {
  try {
    const packageRoot = packageRootFrom(path.join(__dirname, '..'));
    const runtime = ensureRuntime({ packageRoot });
    runPythonCli(runtime, argv);
  } catch (err) {
    process.stderr.write(`supersocks-url-scraper: ${formatError(err)}\n`);
    process.exit(typeof err.exitCode === 'number' ? err.exitCode : 1);
  }
}

if (require.main === module) {
  main();
}

module.exports = { main };
