#!/usr/bin/env node

const assert = require('assert');
const {
  buildReleasePlan,
  compareVersions,
  normalizeVersion,
} = require('./resolve-next-release.cjs');

assert.equal(normalizeVersion('v1.2.3'), '1.2.3');
assert.equal(normalizeVersion('1.2.3-beta.1+build.5'), '1.2.3-beta.1+build.5');
assert(compareVersions('1.2.4', '1.2.3') > 0);
assert(compareVersions('1.2.3', '1.2.3') === 0);
assert(compareVersions('1.2.3-beta.1', '1.2.3') < 0);

const noRelease = buildReleasePlan({
  releaseNeeded: false,
  resolvedVersion: '',
  reason: 'no_release',
  headSha: 'a'.repeat(40),
  latestExistingRelease: 'v24.2.10',
  existingRelease: null,
  tagCommitSha: '',
});
assert.equal(noRelease.release_status, 'release_skipped_no_release_needed');

const sameHeadRerun = buildReleasePlan({
  releaseNeeded: true,
  resolvedVersion: '24.2.11',
  reason: 'next_release_found',
  headSha: 'b'.repeat(40),
  latestExistingRelease: 'v24.2.10',
  existingRelease: { html_url: 'https://example.invalid/release', tag_name: 'v24.2.11' },
  tagCommitSha: 'b'.repeat(40),
});
assert.equal(sameHeadRerun.release_status, 'release_already_exists_for_current_head');

const tagOnlySameHead = buildReleasePlan({
  releaseNeeded: true,
  resolvedVersion: '24.2.11',
  reason: 'next_release_found',
  headSha: 'c'.repeat(40),
  latestExistingRelease: 'v24.2.10',
  existingRelease: null,
  tagCommitSha: 'c'.repeat(40),
});
assert.equal(tagOnlySameHead.release_status, 'release_pending_create');
assert.equal(tagOnlySameHead.tag_status, 'tag_exists_for_current_head');

const pendingCreate = buildReleasePlan({
  releaseNeeded: true,
  resolvedVersion: '24.2.11',
  reason: 'next_release_found',
  headSha: 'd'.repeat(40),
  latestExistingRelease: 'v24.2.10',
  existingRelease: null,
  tagCommitSha: '',
});
assert.equal(pendingCreate.release_status, 'release_pending_create');
assert.equal(pendingCreate.tag_status, 'tag_missing');

assert.throws(
  () =>
    buildReleasePlan({
      releaseNeeded: true,
      resolvedVersion: '24.2.9',
      reason: 'next_release_found',
      headSha: 'e'.repeat(40),
      latestExistingRelease: 'v24.2.10',
      existingRelease: null,
      tagCommitSha: '',
    }),
  /not newer than latest published release/
);

console.log('resolve-next-release smoke test: ok');
