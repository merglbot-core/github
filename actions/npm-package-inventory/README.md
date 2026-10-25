# Project 39 npm package inventory

Read-only input collection for the unresolved nonpersonal npm route. This action
bundles the collector and functional tests from reviewed platform#2628, retaining
the original platform#2300 collector and platform#2301 origin fix. Exact source
hashes and the immutable source commit are recorded in UPSTREAM.json.
It does not fetch code from the private platform repository at runtime.

## Trusted caller contract

| Input scope | Required caller repository | Known packages |
| --- | --- | --- |
| core | merglbot-core/merglbot-admin | auth-common, project-management-app |
| proteinaco | merglbot-proteinaco/proteinaco-web | btf-viz |

Use only a reviewed trusted caller, pinned to a reviewed full commit of this
action. The caller must check out its own repository, supply native npm and
Python 3.11+, and establish its supported existing package-read configuration.
The action reads only the root package-lock.json. It preserves that project's
npm context, including project-local configuration, with private temporary
cache and pack output. It declares no workflow permissions, logs in nowhere and
implements no credential fallback. Never supply a personal token to make it pass.

GITHUB_ACTIONS and GITHUB_REPOSITORY checks are context guards, not authentication
against a runner administrator. The effective credential used by native npm is
not established by a successful read. New caller workflows, package permission
changes, actual runs and source distribution pins need their own review.
Publishing this action activates no caller or package mirror.

The output report_path is a unique file inside RUNNER_TEMP. It contains safe JSON
from the collector and is not printed by this wrapper. A controlled DATA_GAP
retains its failure exit status; command/startup failures can leave an empty
report. A later artifact-upload success must never turn that failure into PASS.
Retain only this report, not npm caches, tarballs or caller config. The caller is
responsible for private artifact access, retention and runner cleanup.

## Evidence scope and limits

The collector preserves every listed version/tag, explicitly retrieving
prereleases omitted by npm's wildcard. It validates each locked archive's
identity, actual compressed bytes and SHA-512 against lock and registry metadata.
It rechecks the catalogue after downloads. Unknown private packages and
incomplete captures fail explicitly. Current supported lockfiles also contain
fixed local links to two declared comments packages. The canonical collector
validates those links against its exact trusted source bindings; they are local
vendored inputs, not additional registry package versions or archive downloads.
Escaping, changed or undeclared lockfile link metadata still fails closed.
This check does not read vendor directories, verify their bytes or attest
filesystem symlink resolution. A concurrent change not observed by these reads
remains outside this nontransactional snapshot's guarantees.

Registry classification uses the actual parsed, percent-decoded and IDNA
normalized hostname; malformed HTTP origins fail explicitly. The separate exact
archive-origin check is unchanged.

Registry and scope-registry flags are fixed to npm.pkg.github.com; native npm
retains responsibility for transport and trusted caller configuration. Scripts
are suppressed for diagnostic downloads. Combined child output and lock input
are limited to 4 MiB, catalogue to 1,000 versions and 1,000 tags, each child to
45 seconds, total npm collection to 300 seconds, with no fetch retries. Private
workspace bytes/files are sampled with a 256 MiB / 4,096-file guard, not a hard
filesystem quota or guarantee against peak overshoot. Child stderr is not echoed.

Only locked archives are downloaded; all_catalogue_archive_bytes_complete and
effective_auth_identity_verified remain false. Historical file sizes, billed
traffic, full-mirror storage, whole budget, consumer cutover and PAT retirement
are separate evidence. Temporary archives/cache are removed on normal cleanup;
an abrupt runner termination requires the runner's own cleanup.

## Local verification and updates

    python3 -m unittest discover -s tests -p test_npm_inventory_action.py

The suite includes the original native npm smoke against an owned authenticated
loopback registry. It proves retained project auth context succeeds and the old
temporary-cwd behavior is rejected. Synthetic archive bytes and integrity are
preserved, including stable/prerelease metadata. Entry-point tests use owned
temporary workspaces and do not reach a provider or read real auth config.

Update snapshots only from a reviewed immutable source, preserving the canonical
context, script, archive and privacy checks, then update hashes and obtain new
exact-head V6 review. A file hash is provenance, not review authorization.
