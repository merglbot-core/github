# Pinned native transaction test dependency

Unmodified files from merglbot-public/docs commit
`9ac0ba9ca03bfbd87103e22b619b8f5fd2004b6d`, path
`tools/session_context_install/`. SHA256 pins are enforced by
`adoption_bundle.verified_sources` before execution. This offline fixture makes
the exact dependency reviewable and exercises the real receipt contract.
It grants no installation authority. The integration test uses temporary files
and simulated launchd; native ACL/xattr/fsync tests require macOS and are
explicitly skipped elsewhere. No live service is called.
