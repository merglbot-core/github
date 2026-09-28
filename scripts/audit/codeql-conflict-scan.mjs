#!/usr/bin/env node
// codeql-conflict-scan — daily, estate-wide, fail-closed.
//
// Detects the class github#789 documented: a repo with GitHub CodeQL DEFAULT SETUP
// `configured` that ALSO carries an advanced CodeQL workflow. GitHub rejects the
// advanced SARIF ("CodeQL analyses from advanced configurations cannot be processed
// when the default setup is enabled"), so the advanced languages silently stop being
// scanned while the default setup's own `Analyze (...)` check stays green.
//
// Why a separate daily tool: the one-shot `fix-codeql-conflict.sh` hardcoded 4 of 14
// orgs and nothing watched continuously — the only conflict in the estate sat for
// three weeks in an org the sweep never reached. The scheduled mode discovers
// orgs through `user/orgs`; an opt-in App canary uses an exact approved scope.
//
// Output: ~/.merglbot/codeql-conflict/latest.json (+ history jsonl). Exit codes:
//   0 = swept, no conflict     2 = conflict(s) found     1 = scan could not complete
// A partial sweep is reported as status=DEGRADED with the unswept repos named; it is
// NOT a clean zero. The autonomy digest renders this file and flags staleness.
//
// Usage: node codeql-conflict-scan.mjs [--json] [--org <login>]... [--quiet]
// Installation canary: --installation-scope <approved.json> --output-dir <isolated-dir>

import { execFileSync } from 'node:child_process';
import { mkdirSync, writeFileSync, appendFileSync, readFileSync,
  mkdtempSync, chmodSync, rmSync } from 'node:fs';
import { homedir, tmpdir } from 'node:os';
import { join, resolve } from 'node:path';

// Filenames from github#789's estate sweep + a content probe for anything else that
// calls the CodeQL analyze action under a different name.
const ADVANCED_NAMES = /^(codeql(-analysis)?|security-codeql)\.ya?ml$/i;
const ANALYZE_ACTION = /github\/codeql-action\/(analyze|init)@/;

const args = process.argv.slice(2);
const wantJson = args.includes('--json');
const quiet = args.includes('--quiet');
const onlyOrgs = [];
let scopeFile, isolatedOutput;
for (let i = 0; i < args.length; i++) {
  const arg = args[i];
  if (arg === '--json' || arg === '--quiet') continue;
  if (!['--org', '--installation-scope', '--output-dir'].includes(arg)
      || !args[i + 1] || args[i + 1].startsWith('--')) {
    throw new Error('invalid_arguments');
  }
  const value = args[++i];
  if (arg === '--org') onlyOrgs.push(value.toLowerCase());
  if (arg === '--installation-scope') {
    if (scopeFile) throw new Error('invalid_arguments');
    scopeFile = value;
  }
  if (arg === '--output-dir') {
    if (isolatedOutput) throw new Error('invalid_arguments');
    isolatedOutput = value;
  }
}
if (Boolean(scopeFile) !== Boolean(isolatedOutput) || scopeFile && onlyOrgs.length) {
  throw new Error('invalid_arguments');
}
const OUT_DIR = isolatedOutput ? resolve(isolatedOutput) : join(homedir(), '.merglbot', 'codeql-conflict');
if (isolatedOutput && OUT_DIR === join(homedir(), '.merglbot', 'codeql-conflict')) {
  throw new Error('output_must_be_isolated');
}
const LATEST = join(OUT_DIR, 'latest.json');
const HISTORY = join(OUT_DIR, 'history.jsonl');
const isolatedGhConfig = scopeFile ? mkdtempSync(join(tmpdir(), 'merglbot-codeql-gh-')) : null;
if (isolatedGhConfig) {
  chmodSync(isolatedGhConfig, 0o700);
  process.on('exit', () => rmSync(isolatedGhConfig, { recursive: true, force: true }));
}

function readInstallationScope(file) {
  const raw = readFileSync(file);
  if (raw.length > 65536) throw new Error('invalid_installation_scope');
  const scope = JSON.parse(raw);
  if (!scope || typeof scope !== 'object' || Array.isArray(scope)
      || Object.keys(scope).sort().join(',') !== 'organization,repositories,version'
      || scope.version !== 1 || typeof scope.organization !== 'string'
      || !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$/.test(scope.organization)
      || scope.organization.toLowerCase() === 'lrtch'
      || !Array.isArray(scope.repositories) || !scope.repositories.length
      || scope.repositories.length > 500) throw new Error('invalid_installation_scope');
  const ids = new Set(), names = new Set();
  for (const repo of scope.repositories) {
    if (!repo || typeof repo !== 'object' || Array.isArray(repo)
        || Object.keys(repo).sort().join(',') !== 'id,name'
        || !Number.isSafeInteger(repo.id) || repo.id <= 0
        || typeof repo.name !== 'string' || !/^[A-Za-z0-9_.-]{1,100}$/.test(repo.name)
        || ids.has(repo.id) || names.has(repo.name.toLowerCase())) {
      throw new Error('invalid_installation_scope');
    }
    ids.add(repo.id); names.add(repo.name.toLowerCase());
  }
  return scope;
}

function installationRepositories(scope) {
  // This endpoint is installation-token-only. Never let gh fall back to its
  // personal keyring when running a canary with a missing explicit token.
  if (!process.env.GH_TOKEN || process.env.GH_HOST && process.env.GH_HOST !== 'github.com') {
    throw new Error('installation_identity_required');
  }
  const pages = JSON.parse(gh(['api', 'installation/repositories?per_page=100', '--paginate', '--slurp']));
  if (!Array.isArray(pages) || !pages.length) throw new Error('invalid_installation_scope');
  const actual = [];
  for (const page of pages) {
    if (!page || !Number.isSafeInteger(page.total_count)
        || page.total_count !== scope.repositories.length
        || !Array.isArray(page.repositories) || !page.repositories.length) {
      throw new Error('installation_scope_mismatch');
    }
    actual.push(...page.repositories);
  }
  const expected = new Map(scope.repositories.map(repo => [repo.id, repo.name.toLowerCase()]));
  const seen = new Set();
  if (actual.length !== expected.size) throw new Error('installation_scope_mismatch');
  for (const repo of actual) {
    if (!repo || !Number.isSafeInteger(repo.id) || seen.has(repo.id)
        || typeof repo.name !== 'string' || typeof repo.full_name !== 'string'
        || repo.full_name.toLowerCase() !== `${scope.organization}/${repo.name}`.toLowerCase()
        || expected.get(repo.id) !== repo.name.toLowerCase()
        || typeof repo.archived !== 'boolean' || typeof repo.disabled !== 'boolean') {
      throw new Error('installation_scope_mismatch');
    }
    seen.add(repo.id);
  }
  if (seen.size !== expected.size) throw new Error('installation_scope_mismatch');
  return actual.filter(repo => !repo.archived && !repo.disabled);
}

function sleepMs(ms) { Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms); }
// One retry on anything that is not a definitive 4xx: the first full sweep lost 8 workflow
// reads and 1 repo to transient failures inside a ~600-call burst (secondary rate limit).
// retry404: the contents API has answered 404 for a file the directory listing returned a
// second earlier (project-management-app/ci.yml, 2026-09-04) — for raw reads a 404 is retried.
function gh(ghArgs, { retry404 = false } = {}) {
  // The canary must not silently pick up a saved personal gh login when an
  // installation lease expires or the environment changes between calls.
  const childEnv = scopeFile ? {
    ...process.env,
    GH_CONFIG_DIR: isolatedGhConfig,
    GITHUB_TOKEN: '', GH_ENTERPRISE_TOKEN: '', GH_PROMPT_DISABLED: '1',
  } : process.env;
  let lastErr;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      return execFileSync('gh', ghArgs, { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'],
        env: childEnv, maxBuffer: 64 * 1024 * 1024 });
    } catch (e) {
      lastErr = e;
      const msg = String(e.stderr || e.message || '');
      const definitive = retry404 ? /HTTP 403/ : /HTTP 40[34]/;
      if (definitive.test(msg) && !/rate limit|abuse|secondary/i.test(msg)) throw e;
      sleepMs(3000 * (attempt + 1));
    }
  }
  throw lastErr;
}
// A workflow that only offers itself via `workflow_call:` (a reusable workflow) never runs
// on its host repo, so it cannot collide with that repo's default setup.
const RUN_TRIGGERS = /^\s*(push|pull_request|pull_request_target|schedule|workflow_dispatch|merge_group):/m;
function isReusableOnly(body) {
  return /^\s*workflow_call:/m.test(body) && !RUN_TRIGGERS.test(body);
}
function ghJson(path, { paginate = false } = {}) {
  const a = ['api', path];
  if (paginate) a.push('--paginate', '--slurp');
  const out = gh(a);
  const parsed = JSON.parse(out);
  if (paginate) return parsed.flat();
  return parsed;
}
function log(msg) { if (!quiet) process.stderr.write(msg + '\n'); }
function safeFailure(error) {
  if (!scopeFile) return String(error.stderr || error.message || error).split('\n')[0];
  const detail = String(error.stderr || error.message || error);
  if (/HTTP 404/.test(detail)) return 'not_found';
  if (/HTTP 40[13]/.test(detail)) return 'auth_or_scope_denied';
  if (/rate limit|secondary|abuse|HTTP 429/i.test(detail)) return 'rate_limited';
  if (/timed? out/i.test(detail)) return 'timeout';
  return 'github_call_failed';
}

const startedAt = new Date().toISOString();
const report = {
  at: startedAt,
  status: 'OK',
  orgs: {},
  repos_active: 0,
  repos_swept: 0,
  default_setup_configured: 0,
  advanced_workflow: 0,
  conflicts: [],
  unswept: [],
  // Repos where this identity cannot READ the code-scanning config (403 "not authorized") —
  // a repo we cannot read is one we could not have mis-configured either; listed, not red.
  unreadable: [],
  errors: [],
};

let orgs, scopedRepositories;
if (scopeFile) {
  try {
    const scope = readInstallationScope(scopeFile);
    scopedRepositories = installationRepositories(scope);
    orgs = [scope.organization];
  } catch (error) {
    report.status = 'ERROR';
    report.errors.push(error?.message === 'installation_scope_mismatch'
      ? 'installation_scope_mismatch' : 'installation_scope_unavailable');
    finish(1);
  }
} else {
  try {
    orgs = ghJson('user/orgs', { paginate: true }).map(o => o.login);
    if (!orgs.length) throw new Error('user/orgs returned an empty list');
  } catch (e) {
    report.status = 'ERROR';
    report.errors.push(`org discovery failed: ${String(e.message || e).split('\n')[0]}`);
    finish(1);
  }
  if (onlyOrgs.length) orgs = orgs.filter(o => onlyOrgs.includes(o.toLowerCase()));
}

for (const org of orgs) {
  let repos;
  try {
    repos = scopedRepositories ?? ghJson(`orgs/${org}/repos?per_page=100&type=all`, { paginate: true })
      .filter(r => !r.archived && !r.disabled);
  } catch (e) {
    report.orgs[org] = { repos: null, error: safeFailure(e) };
    report.unswept.push(`${org}/*`);
    continue;
  }
  const orgRow = { repos: repos.length, default_setup: 0, advanced: 0, conflicts: 0, unswept: 0 };
  report.repos_active += repos.length;

  for (const r of repos) {
    const full = r.full_name;
    let setup = null;
    try {
      setup = ghJson(`repos/${full}/code-scanning/default-setup`);
    } catch (e) {
      const msg = String(e.stderr || e.message || e);
      if (scopeFile && /HTTP 40[34]/.test(msg)) {
        // A scoped token can see repository metadata while lacking this
        // operation's permission. The 404/403 is ambiguous, not a clean no-op.
        if (/not authorized to read code scanning/i.test(msg)) report.unreadable.push(full);
        orgRow.unswept++; report.unswept.push(full);
        report.errors.push(`${full}: default-setup unverified`);
        continue;
      }
      // 404 = code scanning not available; 403 "Code Security must be enabled" = no code scanning
      // at all on this repo. Neither can host a default setup, so neither can conflict: swept.
      if (/HTTP 404/.test(msg) || /Code Security must be enabled/.test(msg)) setup = { state: 'not-configured', languages: [] };
      else if (/not authorized to read code scanning/i.test(msg)) {
        report.unreadable.push(full);
        if (scopeFile) { orgRow.unswept++; report.unswept.push(full); }
        continue;
      }
      else { orgRow.unswept++; report.unswept.push(full); report.errors.push(`${full}: default-setup ${safeFailure(e)}`); continue; }
    }
    const configured = setup.state === 'configured';
    if (configured) { orgRow.default_setup++; report.default_setup_configured++; }

    // Advanced workflow: filename heuristic for every repo; content probe (one call
    // per workflow file) only where default setup is configured — that is the only
    // set in which a hit is a conflict, and it keeps the daily call count small.
    let workflows = [];
    try {
      workflows = ghJson(`repos/${full}/contents/.github/workflows`);
      if (!Array.isArray(workflows)) workflows = [];
    } catch (e) {
      const msg = String(e.stderr || e.message || e);
      if (scopeFile && /HTTP 404/.test(msg)) {
        orgRow.unswept++; report.unswept.push(full);
        report.errors.push(`${full}: workflows unverified`);
        continue;
      }
      if (!/HTTP 404/.test(msg)) { orgRow.unswept++; report.unswept.push(full); report.errors.push(`${full}: workflows ${safeFailure(e)}`); continue; }
    }
    const advancedFiles = workflows.filter(w => ADVANCED_NAMES.test(w.name)).map(w => w.name);
    if (configured && !advancedFiles.length) {
      for (const w of workflows) {
        if (!/\.ya?ml$/i.test(w.name)) continue;
        try {
          const body = gh(['api', `repos/${full}/contents/.github/workflows/${w.name}`, '-H', 'Accept: application/vnd.github.raw'], { retry404: true });
          if (ANALYZE_ACTION.test(body) && !isReusableOnly(body)) advancedFiles.push(w.name);
        } catch (e) {
          // An unreadable workflow leaves the repo's advanced-side UNKNOWN: fail closed, unsweep it.
          orgRow.unswept++; report.unswept.push(full);
          report.errors.push(`${full}: could not read ${w.name}: ${safeFailure(e)}`);
          advancedFiles.length = 0; advancedFiles.push(null); break;
        }
      }
    }
    if (advancedFiles.includes(null)) continue; // unswept above
    if (advancedFiles.length) { orgRow.advanced++; report.advanced_workflow++; }
    report.repos_swept++;

    if (configured && advancedFiles.length) {
      orgRow.conflicts++;
      report.conflicts.push({ repo: full, default_setup_languages: setup.languages || [], advanced_workflows: advancedFiles });
      log(`CONFLICT ${full}: default-setup=${(setup.languages || []).join(',')} advanced=${advancedFiles.join(',')}`);
    }
  }
  report.orgs[org] = orgRow;
  log(`${org}: repos=${orgRow.repos} default_setup=${orgRow.default_setup} advanced=${orgRow.advanced} conflicts=${orgRow.conflicts} unswept=${orgRow.unswept}`);
}

if (report.conflicts.length) report.status = 'CONFLICT';
if (report.unswept.length) report.status = report.conflicts.length ? 'CONFLICT_PARTIAL' : 'DEGRADED';
if (report.repos_swept === 0) { report.status = 'ERROR'; report.errors.push('zero repos swept — silent zero is not a clean zero'); }

finish(report.status === 'ERROR' || report.status === 'DEGRADED' ? 1
  : report.conflicts.length ? 2 : 0);

function finish(code) {
  report.finished_at = new Date().toISOString();
  mkdirSync(OUT_DIR, { recursive: true });
  writeFileSync(LATEST, JSON.stringify(report, null, 2) + '\n');
  appendFileSync(HISTORY, JSON.stringify({ at: report.at, status: report.status, swept: report.repos_swept, active: report.repos_active, conflicts: report.conflicts.map(c => c.repo), unswept: report.unswept.length }) + '\n');
  if (wantJson) process.stdout.write(JSON.stringify(report) + '\n');
  else process.stdout.write(`codeql-conflict-scan ${report.status}: swept ${report.repos_swept}/${report.repos_active} active repos in ${Object.keys(report.orgs).length} orgs; default-setup=${report.default_setup_configured} advanced=${report.advanced_workflow} conflicts=${report.conflicts.length} unswept=${report.unswept.length} unreadable=${report.unreadable.length}\n`);
  process.exit(code);
}
