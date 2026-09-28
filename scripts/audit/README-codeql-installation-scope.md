# CodeQL conflict scanner: installation canary

`codeql-conflict-scan.mjs` preserves its current scheduled `user/orgs` mode.
The source in this repository does not install itself into the local launchd
job. The opt-in `--installation-scope` mode prepares one organization at a time
for Project #39; it must not be described as a completed token cutover.

The trusted caller supplies a reviewed JSON file containing exactly these
fields:

```json
{
  "version": 1,
  "organization": "merglbot-core",
  "repositories": [{"id": 17, "name": "example"}]
}
```

The ID and name above are synthetic test values. For a real canary, bind the
approved organization and repository IDs to one installation grant. The caller
must inject its short-lived installation token as `GH_TOKEN` and specify both
`--installation-scope <file>` and `--output-dir <isolated-dir>`. The output
directory must differ from the ordinary scheduled report location, including
symlink aliases; existing symlinked report files are refused. No token
value belongs in the scope file or command arguments.

Before scanning, the tool refuses a missing `GH_TOKEN`, queries the
installation-only `/installation/repositories` endpoint, and requires its
complete paginated repository ID/name/owner set to equal the reviewed file.
The subprocess uses an empty isolated `gh` config directory, disables prompts
and clears secondary token variables so an expired lease cannot fall back to
the operator's saved `gh` login.
Workflow inspection requires Ruby with its standard YAML/Psych library on the
scheduled host. Keep `codeql_workflow_probe.rb` beside the scanner when installing it;
verify that dependency before replacing the existing scheduled script. The
probe parses YAML structure without executing it, follows reusable workflow
calls, and treats unreadable or unresolved calls as unswept. A sole
`workflow_call` trigger does not execute in the workflow's own repository;
other triggers alongside it do. Commented action references do not count.
Statically disabled jobs and steps are excluded. An unreadable workflow does
not stop inspection of later workflows, so confirmed conflicts remain visible
alongside the incomplete coverage result.
An `init` step without a confirmed `analyze` step is unverified rather than a
proven conflict or a clean zero.
Local composite action references are currently unswept because the scanner
does not inspect their action definitions; a known direct conflict remains in
the report even when another path is unresolved.
It scans only active repositories from that exact set. An unrelated visible
public repository, missing selected repository, wrong installation, permission
failure, or incomplete scan cannot become a clean result. This mode never calls
`/user/orgs` or `/orgs/{org}/repos`; `--org` in the legacy mode still uses
personal-user discovery and is **not** an installation cutover.

For this first canary, an ambiguous `403`/`404` from the CodeQL default-setup
endpoint, or a `404` for the workflow directory, is unswept and nonzero. Those
responses can mean unavailable configuration or missing operation permission;
the scanner cannot distinguish them using its repository listing alone. Before
expanding to repositories where those responses are expected, add an independently
verified permission/absence proof. Never classify the ambiguity as a clean zero.

Before adopting the mode in production, verify the App's repository
Administration:read, Contents:read and Metadata:read permissions, negative
off-target access, token refresh, natural scheduled coverage, and no hidden
personal credential fallback. Run one separately approved grant per
organization; a single installation token cannot cover the entire estate.
The current local launchd source and schedule remain unchanged until a
reviewed installation step and runtime proof are available.
