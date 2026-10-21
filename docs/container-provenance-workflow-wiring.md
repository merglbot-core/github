# Container provenance workflow wiring

The separate source component landed in github#1012 at
`e6b0b8864f92b1453278250ae773d331c7a81165`. The reusable build workflow now
references that immutable source, explicitly selects Python 3.11, and uploads
only the generated temporary paths returned by the action. Existing workflow
outputs retain their names. A pushing build still validates a missing digest;
a non-pushing build without a manifest skips generation and upload.

The binding test checks every referenced action output declaration, parses both
multiline and single-line runner outputs, and compares each JSON value with its
generated file. The local action manifest and executable must match the exact
SHA-256 witnesses read from the pinned source commit. For a future pin bump,
first verify the new landed commit, then update the workflow pin and both source
witnesses together; rerun the binding and action tests. Tests do not fetch code.

The action emits canonical bare SHA-256 subjects with caller-bound metadata.
It does not establish a SLSA assurance level, cryptographic signature, runtime
adoption, or financial savings. Workflow binding tests execute the composite
action locally with isolated fixture files; signing tests use a fake gcloud.
They do not build or push a container or access GCP.

Landing order: verified source github#1012, this separately reviewed protected
workflow proposal, then separately authorized caller adoption and natural
artifact verification. The publication grant D-P-36 excludes this workflow's
merge, caller pin changes, cloud builds/deploys, and external notifications.
Keep the PR in draft until that separate authority is established. No manual
workflow dispatch or production activation is part of this proposal.

Rollback is a separately reviewed revert of this wiring commit before caller
adoption. After adoption, preserve the exact prior caller pins and runtime
state and revalidate the complete rollback scope; do not replay an old script.
