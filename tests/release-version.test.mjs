import assert from 'node:assert/strict';
import { test } from 'node:test';
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { classifyDryRun, determineVersion, validateVersion, runDryRun } from '../scripts/release/determine-version.mjs';
const result = (message, extra = {}) => ({ status: 0, stdout: Buffer.from(message), stderr: Buffer.alloc(0), ...extra });
const log = (message) => `[5:10:00 AM] [semantic-release] › ℹ ${message}\n`;

test('manual SemVer bypasses provider and preserves prerelease/build values', () => {
  for (const version of ['0.0.0', '1.2.3', '2.0.0-rc.1+build.7']) {
    assert.deepEqual(determineVersion(version, () => { throw Error('must not run'); }), { version, skip: false });
  }
});
test('rejects shell syntax, newline output injection and invalid SemVer', () => {
  for (const value of ['', 'v1.2.3', '01.2.3', '1.2.3-01', '1.2.3;false', '1.2.3\nskip=true', '1.2.3$(id)']) {
    assert.throws(() => validateVersion(value));
  }
});
test('upstream success records yield one agreed version', () => {
  assert.deepEqual(classifyDryRun(result(log('The next release version is 1.2.3') + log('Release note for version 1.2.3:'))), { version: '1.2.3', skip: false });
});
test('only a successful explicit no-relevant-change record permits skip', () => {
  assert.deepEqual(classifyDryRun(result(log('There are no relevant changes, so no new version is released.'))), { skip: true });
});
test('nonzero exit with valid-looking output remains failure', () => {
  for (const message of [log('Release note for version 1.2.3:'), log('There are no relevant changes, so no new version is released.')]) {
    assert.throws(() => classifyDryRun(result(message, { status: 1 })));
  }
});
test('interruption, timeout and size overflow remain failure', () => {
  for (const extra of [{ signal: 'SIGKILL' }, { error: new Error('timeout') }, { stdout: Buffer.alloc(1024 * 1024 + 1) }, { stdout: Buffer.alloc(600000), stderr: Buffer.alloc(600000) }]) {
    assert.throws(() => classifyDryRun(result('', extra)));
  }
});
test('empty, unfamiliar, wrong-branch and raw notes do not imply no release', () => {
  for (const message of ['', log('This branch is not configured for release.'), 'Release note for version 1.2.3:', 'There are no relevant changes, so no new version is released.']) {
    assert.throws(() => classifyDryRun(result(message)));
  }
});
test('contradictory outcome records and versions fail closed', () => {
  assert.throws(() => classifyDryRun(result(log('Release note for version 1.2.3:') + log('Release note for version 2.0.0:'))));
  assert.throws(() => classifyDryRun(result(log('Release note for version 1.2.3:') + log('There are no relevant changes, so no new version is released.'))));
});

test('actual CLI manual success writes only validated outputs', () => {
  const dir = mkdtempSync(join(tmpdir(), 'release-version-test-'));
  try {
    const out = join(dir, 'output');
    const child = spawnSync(process.execPath, [fileURLToPath(new URL('../scripts/release/determine-version.mjs', import.meta.url))],
      { env: { ...process.env, GITHUB_OUTPUT: out, RELEASE_VERSION_INPUT: '1.2.3-rc.1' }, timeout: 5000 });
    assert.equal(child.status, 0);
    assert.equal(readFileSync(out, 'utf8'), 'version=1.2.3-rc.1\nskip=false\n');
    assert.equal(child.stdout.length + child.stderr.length, 0);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
test('actual provider failure cannot write skip or reveal captured details', () => {
  const dir = mkdtempSync(join(tmpdir(), 'release-version-test-'));
  try {
    const out = join(dir, 'output');
    writeFileSync(out, 'existing=preserved\n');
    writeFileSync(join(dir, 'npx'), '#!/bin/sh\nprintf "%s\n" "PROVIDER_DETAILS_MUST_STAY_PRIVATE" >&2\nexit 1\n', { mode: 0o700 });
    const child = spawnSync(process.execPath, [fileURLToPath(new URL('../scripts/release/determine-version.mjs', import.meta.url))],
      { env: { ...process.env, PATH: dir, GITHUB_OUTPUT: out, RELEASE_VERSION_INPUT: '' }, timeout: 5000 });
    assert.equal(child.status, 1);
    assert.equal(readFileSync(out, 'utf8'), 'existing=preserved\n');
    assert.equal(child.stdout.length, 0);
    assert.ok(!child.stderr.toString().includes('PROVIDER_DETAILS'));
    assert.match(child.stderr.toString(), /Release version determination failed/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});

function fixtureProvider(dir, overflow) {
  const pidFile = join(dir, 'descendant.pid');
  const script = `#!${process.execPath}\n` +
    `const { spawn } = require('node:child_process');\n` +
    `const { writeFileSync } = require('node:fs');\n` +
    `const child = spawn(process.execPath, ['-e', 'setTimeout(() => {}, 30000)'], { stdio: 'ignore' });\n` +
    `writeFileSync(${JSON.stringify(pidFile)}, String(child.pid));\n` +
    (overflow ? `process.stdout.write('x'.repeat(2048));\n` : '') +
    `setTimeout(() => {}, 30000);\n`;
  writeFileSync(join(dir, 'npx'), script, { mode: 0o700 });
  return pidFile;
}
for (const overflow of [false, true]) {
  test(`actual ${overflow ? 'output overflow' : 'timeout'} stops provider group descendant`, async () => {
    const dir = mkdtempSync(join(tmpdir(), 'release-version-tree-'));
    const oldPath = process.env.PATH;
    const pidFile = fixtureProvider(dir, overflow);
    let pid;
    try {
      process.env.PATH = dir;
      const actual = await runDryRun({ timeoutMs: 1000, outputLimit: 1024 });
      assert.equal(actual.error, true);
      pid = Number(readFileSync(pidFile, 'utf8'));
      assert.ok(Number.isSafeInteger(pid) && pid > 1);
      const observed = spawnSync('/bin/ps', ['-p', String(pid), '-o', 'stat='], { timeout: 2000 });
      const state = observed.stdout.toString().trim();
      assert.ok(state === '' || state.startsWith('Z'), `fixture descendant still runnable: ${state}`);
    } finally {
      process.env.PATH = oldPath;
      if (pid) { try { process.kill(pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; } }
      rmSync(dir, { recursive: true, force: true });
    }
  });
}

for (const [message, expected] of [
  ['Release note for version 1.2.3:', 'version=1.2.3\nskip=false\n'],
  ['There are no relevant changes, so no new version is released.', 'skip=true\n'],
]) {
  test(`actual CLI accepts successful automatic outcome: ${expected.split('\n')[0]}`, () => {
    const dir = mkdtempSync(join(tmpdir(), 'release-version-success-'));
    try {
      const out = join(dir, 'output');
      writeFileSync(join(dir, 'npx'), `#!${process.execPath}\nprocess.stdout.write(${JSON.stringify(log(message))});\n`, { mode: 0o700 });
      const child = spawnSync(process.execPath, [fileURLToPath(new URL('../scripts/release/determine-version.mjs', import.meta.url))],
        { env: { ...process.env, PATH: dir, GITHUB_OUTPUT: out, RELEASE_VERSION_INPUT: '' }, timeout: 5000 });
      assert.equal(child.status, 0);
      assert.equal(readFileSync(out, 'utf8'), expected);
      assert.equal(child.stdout.length + child.stderr.length, 0);
    } finally { rmSync(dir, { recursive: true, force: true }); }
  });
}
