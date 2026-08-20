'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');

const ROOT = path.resolve(__dirname, '../..');
const COMPOSE = path.join(ROOT, 'docker-compose.yml');

test('docker-compose.yml disables core dumps for the scraper service', () => {
  assert.ok(fs.existsSync(COMPOSE), `missing ${COMPOSE}`);
  const text = fs.readFileSync(COMPOSE, 'utf8');

  // The ulimit must be declared on the supersocks-url-scraper service only.
  assert.match(text, /supersocks-url-scraper:/, 'scraper service must be present');
  assert.match(text, /\bulimits:/, 'must declare a ulimits block');
  assert.match(text, /\bcore:/, 'must set the core ulimit');
  // soft=0 hard=0 (YAML ints, like compose renders them)
  assert.match(text, /soft:\s*0/, 'core soft limit must be 0');
  assert.match(text, /hard:\s*0/, 'core hard limit must be 0');
});
