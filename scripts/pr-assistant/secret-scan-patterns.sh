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
#
# NOTE (reference-only): workflows inline these patterns to avoid `source`-ing scripts
# from the repo checkout (RCE/exfil risk on PR branches). Keep this file aligned with:
# - .github/workflows/merglbot-pr-v3-on-demand.yml

_GH="gh"
_GHP="${_GH}""p_"
_GH_TOKEN_PREFIX="${_GH}""[oprsut]_"
_GITHUB_PAT_PREFIX="github""_pat_"
_SK_PREFIX="s""k-"

_BEGIN_PRIVATE_KEY='-----BEGIN [A-Z ]*PRIVATE KEY-----'
_BEGIN_PGP_PRIVATE_KEY='-----BEGIN PGP[ ]PRIVATE[ ]KEY[ ]BLOCK-----'
_SLACK_TOKEN='(^|[^[:alnum:]_])xox[baprs]-[A-Za-z0-9-]{10,}($|[^[:alnum:]_])'
_GITHUB_TOKEN_CLASSIC="(^|[^[:alnum:]_])${_GHP}[A-Za-z0-9]{30,}($|[^[:alnum:]_])"
_GITHUB_TOKEN_FINE_GRAINED="(^|[^[:alnum:]_])${_GITHUB_PAT_PREFIX}[A-Za-z0-9_]{20,}($|[^[:alnum:]_])"
_GITHUB_TOKEN_GENERIC="(^|[^[:alnum:]_])${_GH_TOKEN_PREFIX}[A-Za-z0-9]{30,}($|[^[:alnum:]_])"
_GENERIC_KEY="(^|[^[:alnum:]_])key_[A-Za-z0-9_-]{20,}($|[^[:alnum:]_])"
_AWS_ACCESS_KEY="(^|[^[:alnum:]_])AKIA[0-9A-Z]{16}($|[^[:alnum:]_])"
_GOOGLE_API_KEY="(^|[^[:alnum:]_])AIza[0-9A-Za-z_-]{30,}($|[^[:alnum:]_])"
_GOOGLE_OAUTH_REFRESH="(^|[^[:alnum:]_])ya29\.[A-Za-z0-9_-]{20,}($|[^[:alnum:]_])"
_OPENAI_PROJECT_KEY="(^|[^[:alnum:]_])${_SK_PREFIX}(proj|ant)-[A-Za-z0-9_-]{20,}($|[^[:alnum:]_])"
_OPENAI_KEY="(^|[^[:alnum:]_])${_SK_PREFIX}[A-Za-z0-9_-]{30,}($|[^[:alnum:]_])"

SECRET_PATTERN_STRICT="${_BEGIN_PRIVATE_KEY}|${_BEGIN_PGP_PRIVATE_KEY}|${_SLACK_TOKEN}|${_GITHUB_TOKEN_CLASSIC}|${_GITHUB_TOKEN_FINE_GRAINED}|${_GITHUB_TOKEN_GENERIC}|${_GENERIC_KEY}|${_AWS_ACCESS_KEY}|${_GOOGLE_API_KEY}|${_GOOGLE_OAUTH_REFRESH}|${_OPENAI_PROJECT_KEY}|${_OPENAI_KEY}"

# The `key_...` heuristic is intentionally broad (aligns with runtime redaction)
# and can match unrelated lockfile content. Exclude it from pr_diff_full.txt
# (full lockfile diffs) to reduce false positives that would otherwise suppress
# AI review unnecessarily.
SECRET_PATTERN_STRICT_NO_KEY="${_BEGIN_PRIVATE_KEY}|${_BEGIN_PGP_PRIVATE_KEY}|${_SLACK_TOKEN}|${_GITHUB_TOKEN_CLASSIC}|${_GITHUB_TOKEN_FINE_GRAINED}|${_GITHUB_TOKEN_GENERIC}|${_AWS_ACCESS_KEY}|${_GOOGLE_API_KEY}|${_GOOGLE_OAUTH_REFRESH}|${_OPENAI_PROJECT_KEY}|${_OPENAI_KEY}"

JWT_PATTERN_STRICT='(^|[^[:alnum:]_])eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}($|[^[:alnum:]_])'
