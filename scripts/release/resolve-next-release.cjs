#!/usr/bin/env node

const { execFileSync } = require('child_process');

const SEMVER_RE = /^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$/;

function normalizeVersion(input) {
  if (!input) {
    return '';
  }

  const trimmed = String(input).trim();
  const match = SEMVER_RE.exec(trimmed);
  if (!match) {
    throw new Error(`Invalid semantic version: ${trimmed}`);
  }

  return `${match[1]}.${match[2]}.${match[3]}${match[4] ? `-${match[4]}` : ''}${match[5] ? `+${match[5]}` : ''}`;
}

function parseSemver(version) {
  const normalized = normalizeVersion(version);
  const match = SEMVER_RE.exec(normalized);
  return {
    major: Number(match[1]),
    minor: Number(match[2]),
    patch: Number(match[3]),
    prerelease: match[4] ? match[4].split('.') : [],
  };
}

function compareIdentifiers(left, right) {
  const leftNumeric = /^\d+$/.test(left);
  const rightNumeric = /^\d+$/.test(right);

  if (leftNumeric && rightNumeric) {
    return Number(left) - Number(right);
  }

  if (leftNumeric) {
    return -1;
  }

  if (rightNumeric) {
    return 1;
  }

  if (left < right) {
    return -1;
  }

  if (left > right) {
    return 1;
  }

  return 0;
}

function comparePrerelease(left, right) {
  if (left.length === 0 && right.length === 0) {
    return 0;
  }

  if (left.length === 0) {
    return 1;
  }

  if (right.length === 0) {
    return -1;
  }

  const len = Math.max(left.length, right.length);
  for (let idx = 0; idx < len; idx += 1) {
    const leftId = left[idx];
    const rightId = right[idx];

    if (leftId === undefined) {
      return -1;
    }

    if (rightId === undefined) {
      return 1;
    }

    const cmp = compareIdentifiers(leftId, rightId);
    if (cmp !== 0) {
      return cmp;
    }
  }

  return 0;
}

function compareVersions(left, right) {
  const leftParsed = parseSemver(left);
  const rightParsed = parseSemver(right);

  for (const key of ['major', 'minor', 'patch']) {
    if (leftParsed[key] !== rightParsed[key]) {
      return leftParsed[key] - rightParsed[key];
    }
  }

  return comparePrerelease(leftParsed.prerelease, rightParsed.prerelease);
}

function commandOutput(command, args, options = {}) {
  try {
    return execFileSync(command, args, {
      cwd: options.cwd || process.cwd(),
      env: options.env || process.env,
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe'],
    }).trim();
  } catch (error) {
    if (options.allowFailure) {
      return '';
    }

    const stderr = error.stderr ? String(error.stderr).trim() : '';
    throw new Error(stderr || error.message);
  }
}

function ghApi(path, allowFailure = false) {
  const env = {
    ...process.env,
    GH_TOKEN: process.env.GH_TOKEN || process.env.GITHUB_TOKEN || '',
  };
  const raw = commandOutput('gh', ['api', path], { env, allowFailure });
  return raw ? JSON.parse(raw) : null;
}

function getHeadSha() {
  return process.env.GITHUB_SHA || commandOutput('git', ['rev-parse', 'HEAD']);
}

function getLatestExistingRelease(repository) {
  return ghApi(`repos/${repository}/releases/latest`, true);
}

function getReleaseByTag(repository, tagName) {
  return ghApi(`repos/${repository}/releases/tags/${tagName}`, true);
}

function getTagCommitSha(tagName) {
  return commandOutput('git', ['rev-list', '-n', '1', tagName], { allowFailure: true });
}

function buildReleasePlan({
  releaseNeeded,
  resolvedVersion,
  reason,
  headSha,
  latestExistingRelease,
  existingRelease,
  tagCommitSha,
}) {
  if (!releaseNeeded) {
    return {
      release_needed: false,
      resolved_version: '',
      tag_name: '',
      reason: reason || 'no_release',
      release_status: 'release_skipped_no_release_needed',
      tag_status: 'tag_not_applicable',
      release_url: '',
      tag_commit_sha: '',
      latest_existing_release: latestExistingRelease || '',
    };
  }

  const normalizedVersion = normalizeVersion(resolvedVersion);
  const tagName = `v${normalizedVersion}`;
  const latestExisting = latestExistingRelease || '';
  const existingReleaseTag = existingRelease?.tag_name || '';
  const existingReleaseUrl = existingRelease?.html_url || '';

  if (existingReleaseTag || tagCommitSha) {
    if (tagCommitSha && tagCommitSha === headSha) {
      if (existingReleaseTag) {
        return {
          release_needed: true,
          resolved_version: normalizedVersion,
          tag_name: tagName,
          reason: reason || 'release_already_exists_for_current_head',
          release_status: 'release_already_exists_for_current_head',
          tag_status: 'tag_exists_for_current_head',
          release_url: existingReleaseUrl,
          tag_commit_sha: tagCommitSha,
          latest_existing_release: latestExisting,
        };
      }

      return {
        release_needed: true,
        resolved_version: normalizedVersion,
        tag_name: tagName,
        reason: reason || 'tag_exists_for_current_head_release_missing',
        release_status: 'release_pending_create',
        tag_status: 'tag_exists_for_current_head',
        release_url: '',
        tag_commit_sha: tagCommitSha,
        latest_existing_release: latestExisting,
      };
    }

    throw new Error(
      `Resolved version ${tagName} already exists on a different commit (${tagCommitSha || 'unknown'}), current HEAD is ${headSha}.`
    );
  }

  if (latestExisting) {
    const latestNormalized = normalizeVersion(latestExisting);
    if (compareVersions(normalizedVersion, latestNormalized) <= 0) {
      throw new Error(
        `Resolved version ${tagName} is not newer than latest published release ${latestExisting}; failing closed.`
      );
    }
  }

  return {
    release_needed: true,
    resolved_version: normalizedVersion,
    tag_name: tagName,
    reason: reason || 'next_release_found',
    release_status: 'release_pending_create',
    tag_status: 'tag_missing',
    release_url: '',
    tag_commit_sha: '',
    latest_existing_release: latestExisting,
  };
}

async function resolveWithSemanticRelease() {
  const semanticReleaseModule = require('semantic-release');
  const semanticRelease = semanticReleaseModule.default || semanticReleaseModule;
  const repositoryUrl = process.env.GITHUB_SERVER_URL && process.env.GITHUB_REPOSITORY
    ? `${process.env.GITHUB_SERVER_URL}/${process.env.GITHUB_REPOSITORY}.git`
    : undefined;

  const result = await semanticRelease(
    {
      branches: ['main'],
      ci: false,
      dryRun: true,
      repositoryUrl,
    },
    {
      cwd: process.cwd(),
      env: process.env,
      stdout: process.stdout,
      stderr: process.stderr,
    }
  );

  if (!result || !result.nextRelease || !result.nextRelease.version) {
    return {
      releaseNeeded: false,
      resolvedVersion: '',
      reason: 'no_release',
    };
  }

  return {
    releaseNeeded: true,
    resolvedVersion: normalizeVersion(result.nextRelease.version),
    reason: 'next_release_found',
  };
}

async function main() {
  const manualInput = normalizeVersion(process.env.RELEASE_INPUT_VERSION || '');
  const repository = process.env.GITHUB_REPOSITORY;

  if (!repository) {
    throw new Error('GITHUB_REPOSITORY is required.');
  }

  const headSha = getHeadSha();
  const latestRelease = getLatestExistingRelease(repository);
  const latestExistingRelease = latestRelease?.tag_name || '';

  const resolution = manualInput
    ? {
        releaseNeeded: true,
        resolvedVersion: manualInput,
        reason: 'manual_input',
      }
    : await resolveWithSemanticRelease();

  const tagName = resolution.releaseNeeded ? `v${resolution.resolvedVersion}` : '';
  const existingRelease = tagName ? getReleaseByTag(repository, tagName) : null;
  const tagCommitSha = tagName ? getTagCommitSha(tagName) : '';

  const plan = buildReleasePlan({
    releaseNeeded: resolution.releaseNeeded,
    resolvedVersion: resolution.resolvedVersion,
    reason: resolution.reason,
    headSha,
    latestExistingRelease,
    existingRelease,
    tagCommitSha,
  });

  process.stdout.write(`${JSON.stringify(plan, null, 2)}\n`);
}

if (require.main === module) {
  main().catch((error) => {
    console.error(`resolve-next-release: ${error.message}`);
    process.exit(1);
  });
}

module.exports = {
  buildReleasePlan,
  compareVersions,
  normalizeVersion,
};
