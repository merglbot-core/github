import { spawnSync } from 'node:child_process';
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
  if (outputs.some((value) => !Buffer.isBuffer(value) || value.length > LIMIT)) {
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

export function main(env = process.env) {
  try {
    if (!env.GITHUB_OUTPUT) throw new Error('RELEASE_OUTPUT_MISSING');
    const result = determineVersion(env.RELEASE_VERSION_INPUT ?? '', () => spawnSync(
      'npx', ['--no-install', 'semantic-release', '--dry-run', '--no-ci'],
      { stdio: ['ignore', 'pipe', 'pipe'], maxBuffer: LIMIT, timeout: 120000, killSignal: 'SIGKILL' },
    ));
    appendFileSync(env.GITHUB_OUTPUT, result.skip ? 'skip=true\n' : `version=${result.version}\nskip=false\n`);
    return 0;
  } catch {
    // Provider output, exception details and manual input may contain sensitive data.
    console.error('::error::Release version determination failed; no release outcome was accepted.');
    return 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = main();
}
