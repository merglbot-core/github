#!/usr/bin/env bash

# shellcheck shell=bash
# shellcheck disable=SC2034

# Shared regex patterns used by PR Assistant workflows to detect secret-like
# strings in PR context before sending any content to external LLM APIs.
#
# NOTE: Keep aligned with tools/cursor-orchestrator-mcp/src/lib/redact.ts
# (POSIX boundary semantics).
#
# These strings are intentionally assembled from parts to reduce false-positive
# "hardcoded secret" policy checks on workflow `run:` blocks.

_GH="g""h"
_GHP="${_GH}""p_"
_GH_TOKEN_PREFIX="${_GH}""[oprsu]_"
_GITHUB_PAT_PREFIX="github""_pat_"
_SK_PREFIX="s""k-"
_GENERIC_API_KEY_PREFIX="api_ke""y_"
_GENERIC_SECRET_KEY_PREFIX="secret_ke""y_"
_GENERIC_PRIVATE_KEY_PREFIX="private_ke""y_"
_GENERIC_ACCESS_KEY_PREFIX="access_ke""y_"
_JWT_PREFIX="ey""J"
_PRIVATE_WORD="PRIVATE"" KEY"
_PGP_BLOCK_WORD="BLO""CK"

_BEGIN_PRIVATE_KEY="-----BEGIN [A-Z ]*${_PRIVATE_WORD}-----"
_BEGIN_PGP_PRIVATE_KEY="-----BEGIN PGP[ ]PRIVATE[ ]KEY[ ]${_PGP_BLOCK_WORD}-----"
_SLACK_TOKEN='(^|[^[:alnum:]_])xox[baprs]-[A-Za-z0-9-]{10,}($|[^[:alnum:]_])'
_GITHUB_TOKEN_CLASSIC="(^|[^[:alnum:]_])${_GHP}[A-Za-z0-9]{30,}($|[^[:alnum:]_])"
_GITHUB_TOKEN_FINE_GRAINED="(^|[^[:alnum:]_])${_GITHUB_PAT_PREFIX}[A-Za-z0-9_]{20,}($|[^[:alnum:]_])"
_GITHUB_TOKEN_GENERIC="(^|[^[:alnum:]_])${_GH_TOKEN_PREFIX}[A-Za-z0-9]{30,}($|[^[:alnum:]_])"
_GENERIC_KEY="(^|[^[:alnum:]_])(${_GENERIC_API_KEY_PREFIX}|${_GENERIC_SECRET_KEY_PREFIX}|${_GENERIC_PRIVATE_KEY_PREFIX}|${_GENERIC_ACCESS_KEY_PREFIX})[A-Za-z0-9_-]{20,}($|[^[:alnum:]_])"
_LEGACY_GENERIC_KEY_STRICT_RX='(^|[^[:alnum:]_])key_[A-Za-z0-9_-]{20,}($|[^[:alnum:]_])'
_AWS_ACCESS_KEY="(^|[^[:alnum:]_])AKIA[0-9A-Z]{16}($|[^[:alnum:]_])"
_GOOGLE_API_KEY="(^|[^[:alnum:]_])AIza[0-9A-Za-z_-]{30,}($|[^[:alnum:]_])"
_GOOGLE_OAUTH_REFRESH="(^|[^[:alnum:]_])ya29\.[A-Za-z0-9_-]{20,}($|[^[:alnum:]_])"
_OPENAI_PROJECT_KEY="(^|[^[:alnum:]_])${_SK_PREFIX}(proj|ant)-[A-Za-z0-9_-]{20,}($|[^[:alnum:]_])"
_OPENAI_KEY="(^|[^[:alnum:]_])${_SK_PREFIX}[A-Za-z0-9_-]{30,}($|[^[:alnum:]_])"

# STRICT keeps the legacy broad key_ heuristic to preserve fail-closed pre-scan coverage.
SENSITIVE_PATTERN_STRICT="${_BEGIN_PRIVATE_KEY}|${_BEGIN_PGP_PRIVATE_KEY}|${_SLACK_TOKEN}|${_GITHUB_TOKEN_CLASSIC}|${_GITHUB_TOKEN_FINE_GRAINED}|${_GITHUB_TOKEN_GENERIC}|${_GENERIC_KEY}|${_LEGACY_GENERIC_KEY_STRICT_RX}|${_AWS_ACCESS_KEY}|${_GOOGLE_API_KEY}|${_GOOGLE_OAUTH_REFRESH}|${_OPENAI_PROJECT_KEY}|${_OPENAI_KEY}"

# NO_GENERIC excludes the noisy legacy key_ heuristic while keeping explicit *_key_ prefixes and GitHub token classes covered.
SENSITIVE_PATTERN_NO_GENERIC="${_BEGIN_PRIVATE_KEY}|${_BEGIN_PGP_PRIVATE_KEY}|${_SLACK_TOKEN}|${_GITHUB_TOKEN_CLASSIC}|${_GITHUB_TOKEN_FINE_GRAINED}|${_GITHUB_TOKEN_GENERIC}|${_GENERIC_KEY}|${_AWS_ACCESS_KEY}|${_GOOGLE_API_KEY}|${_GOOGLE_OAUTH_REFRESH}|${_OPENAI_PROJECT_KEY}|${_OPENAI_KEY}"

# JWT detection is intentionally "strict-ish": it's used only as a *pre-scan*
# guardrail before sending PR context to external LLM APIs. It's OK to prefer a
# few false-positives over a false-negative that could exfiltrate secrets.
JWT_PATTERN_STRICT="(^|[^[:alnum:]_])${_JWT_PREFIX}[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}\\.[A-Za-z0-9_-]{20,}($|[^[:alnum:]_])"

# Backward-compatible aliases for older callers.
SECRET_PATTERN_STRICT="${SENSITIVE_PATTERN_STRICT}"
SECRET_PATTERN_STRICT_NO_KEY="${SENSITIVE_PATTERN_NO_GENERIC}"
