import { spawn } from 'node:child_process';
import { appendFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';

const NUMBER = '(?:0|[1-9][0-9]*)';
const PRE = '(?:0|[1-9][0-9]*|[0-9]*[A-Za-z-][0-9A-Za-z-]*)';
const VERSION = new RegExp(`^${NUMBER}\\.${NUMBER}\\.${NUMBER}(?:-${PRE}(?:\\.${PRE})*)?(?:\\+[0-9A-Za-z-]+(?:\\.[0-9A-Za-z-]+)*)?$`);
const LIMIT = 1024 * 1024;

export function validateVersion(value) {
  if (typeof value !== 'string' || value.length > 128 || !VERSION.test(value)) {
    throw new Error('RELEASE_VERSION_INVALID');
  }
  return value;
}

export function classifyDryRun(result) {
  // A failed, interrupted or oversized provider output never means no release.
  if (!result || result.error || result.signal || result.status !== 0) {
    throw new Error('RELEASE_DRY_RUN_FAILED');
  }
  const outputs = [result.stdout, result.stderr];
  if (outputs.some((value) => !Buffer.isBuffer(value) || value.length > LIMIT)
      || outputs.reduce((sum, value) => sum + value.length, 0) > LIMIT) {
    throw new Error('RELEASE_DRY_RUN_OUTPUT_INVALID');
  }
  const lines = Buffer.concat(outputs).toString('utf8').replace(/\x1b\[[0-9;]*m/g, '').split(/\r?\n/);
  const versions = new Set();
  let noRelevantChanges = false;
  for (const line of lines) {
    // Only the upstream logger's own outcome records, not arbitrary release notes.
    if (!/^\[[^\]\r\n]+\] \[semantic-release\] /.test(line)) continue;
    if (line.includes('There are no relevant changes, so no new version is released.')) {
      noRelevantChanges = true;
    }
    const match = line.match(/(?:The next release version is|Release note for version|Published release) ([^\s:]+)(?=[:\s]|$)/);
    if (match) versions.add(validateVersion(match[1]));
  }
  if (versions.size === 1 && !noRelevantChanges) return { version: [...versions][0], skip: false };
  if (versions.size === 0 && noRelevantChanges) return { skip: true };
  throw new Error('RELEASE_DRY_RUN_OUTCOME_UNPROVEN');
}

export function determineVersion(manualVersion, run) {
  if (typeof manualVersion !== 'string') throw new Error('RELEASE_VERSION_INVALID');
  if (manualVersion !== '') return { version: validateVersion(manualVersion), skip: false };
  return classifyDryRun(run());
}

export function runDryRun({ timeoutMs = 120000, outputLimit = LIMIT } = {}) {
  // Test overrides may tighten bounds only. The CLI exposes neither override.
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0 || timeoutMs > 120000
      || !Number.isInteger(outputLimit) || outputLimit <= 0 || outputLimit > LIMIT) {
    return Promise.resolve({ error: true });
  }
  return new Promise((resolve) => {
    // Explicit analysis options apply only to this invocation, not existing workflows.
    const child = spawn('npx', ['--no-install', 'semantic-release', '--dry-run', '--no-ci',
      '--branches', 'main', '--plugins', '@semantic-release/commit-analyzer,@semantic-release/release-notes-generator'],
      { stdio: ['ignore', 'pipe', 'pipe'], detached: true });
    const stdout = [], stderr = [];
    let bytes = 0, failed = false, done = false, deadline, drainDeadline;
    const killGroup = () => {
      if (!child.pid) return;
      try { process.kill(-child.pid, 'SIGKILL'); } catch (error) {
        if (error.code !== 'ESRCH') failed = true;
      }
    };
    const finish = (status, signal) => {
      if (done) return;
      done = true;
      clearTimeout(deadline);
      clearTimeout(drainDeadline);
      resolve(failed ? { error: true } : {
        status, signal, stdout: Buffer.concat(stdout), stderr: Buffer.concat(stderr),
      });
    };
    const stop = () => {
      failed = true;
      stdout.length = 0;
      stderr.length = 0;
      killGroup();
      child.stdout.destroy();
      child.stderr.destroy();
      drainDeadline ??= setTimeout(() => finish(null, 'SIGKILL'), 2000);
    };
    const collect = (target) => (chunk) => {
      if (failed || done) return;
      bytes += chunk.length;
      if (bytes > outputLimit) { stop(); return; }
      target.push(chunk);
    };
    child.stdout.on('data', collect(stdout));
    child.stderr.on('data', collect(stderr));
    child.stdout.on('error', stop);
    child.stderr.on('error', stop);
    child.on('error', () => { failed = true; finish(null, null); });
    child.on('exit', () => {
      // A completed CLI must not leave same-group descendants holding pipes open.
      killGroup();
      drainDeadline ??= setTimeout(stop, 2000);
    });
    child.on('close', finish);
    deadline = setTimeout(stop, timeoutMs);
  });
}

export async function main(env = process.env) {
  try {
    if (!env.GITHUB_OUTPUT) throw new Error('RELEASE_OUTPUT_MISSING');
    const manual = env.RELEASE_VERSION_INPUT ?? '';
    const result = manual !== ''
      ? { version: validateVersion(manual), skip: false }
      : classifyDryRun(await runDryRun());
    appendFileSync(env.GITHUB_OUTPUT, result.skip ? 'skip=true\n' : `version=${result.version}\nskip=false\n`);
    return 0;
  } catch {
    // Provider output, exception details and manual input may contain sensitive data.
    console.error('::error::Release version determination failed; no release outcome was accepted.');
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main();
}
