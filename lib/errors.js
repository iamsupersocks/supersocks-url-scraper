'use strict';

function fail(message, exitCode = 1) {
  const err = new Error(message);
  err.exitCode = exitCode;
  throw err;
}

function formatError(err) {
  if (!err) {
    return 'Unknown error';
  }
  const message = String(err.message || err).trim();
  return message || 'Unknown error';
}

module.exports = {
  fail,
  formatError,
};
