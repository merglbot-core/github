#!/usr/bin/env python3
"""Verify the latest Merglbot PR Assistant current-head review receipt.

Two receipt eras are supported, because they publish on different surfaces:

* **v6 (the active gate).** The receipt is the ``output.summary`` of the required
  ``Merglbot PR Assistant v6`` check-run on the live head, **bound to the
  producing GitHub App identity** — the check-run name is a routing key that any
  ``checks: write`` identity can claim, so it is never the trust root. A PR comment is
  OPTIONAL under v6 and is absent by design on low-risk single-engine reviews,
  so this era is never verified through comments. The v6 receipt does not carry
  ``MERGLBOT_PR_CHECK_SURFACE``, ``MERGLBOT_FOLLOW_UP_ID`` or
  ``MERGLBOT_DOCUMENTATION_OBLIGATION_STATE`` (those exist only on the optional
  comment), and no v6 surface emits ``MERGLBOT_RUN_URL`` at all — requiring any
  of them would fail-close every valid v6 receipt.
* **v3 (historical).** The receipt is the latest trusted ``github-actions[bot]``
  PR comment tagged ``<!-- MERGLBOT_PR_ASSISTANT_V3 -->``, which does carry the
  four markers above.

The script intentionally reads public GitHub PR/check/comment truth through `gh`
and prints one JSON object. It does not mutate GitHub state.

Canonical contracts: ``MERGLBOT_PR_ASSISTANT_V6.md`` sections 3 and 8, and
``PR_POLICY.md`` section 3.6 (era-scoped receipt-surface rule).
"""

# Managed rollout artifact copied into repositories with different Black configs.
# NOTE: the merglbot-core/github rollout source additionally carries a v4 canary
# branch that was never propagated here; this docs-repo copy covers the v3 and
# v6 eras only.
# fmt: off

from __future__ import annotations

import argparse
from pathlib import Path
import json
import re
import subprocess
from typing import Any, Callable

MARKER_RE = re.compile(r"<!--\s*(MERGLBOT_[A-Z0-9_]+)\s*:\s*([\s\S]*?)\s*-->")
SECTION_HEADER_RE = re.compile(r"^#{2,6}\s+")
ZAVER_SECTION_HEADER_RE = re.compile(r"^##\s+")
MACHINE_TOKEN_STRIP_RE = re.compile(r"[^a-z0-9_]+")
BLOCKING_FINDINGS_COUNT_RE = re.compile(r"^[1-9]")
PR_ASSISTANT_WORKFLOW_PATHS = {
    ".github/workflows/merglbot-pr-assistant-v3-on-demand.yml",
    ".github/workflows/merglbot-pr-v3-on-demand.yml",
}
# The literal required-check name is a de-facto interface (V6 doc section 9).
# It is a ROUTING key, never a trust root: any identity holding `checks: write`
# on the repository can publish a check run under this exact name. The trust
# root is the producing GitHub App identity below.
V6_CHECK_NAME = "Merglbot PR Assistant v6"
# Trusted producer of the required v6 check run. `app.id` is the trust root
# because it is immutable across GitHub App renames, while `app.slug` is not;
# the slug and owner are asserted as well so that a rename, a re-created app, or
# an installation from another org fails CLOSED (visible blocker) instead of
# being silently accepted or silently ignored. Rotating the app therefore
# requires updating these constants and `pr-assistant/receipt-verification.md`
# in the same change. Verified live against four consecutive
# merglbot-public/docs v6 check runs (app.id 3518182, slug
# merglbot-pr-assistant-v4-stg owned by merglbot-core — the `v4` in the slug is
# era-of-origin naming; the app hosts the v6 runtime).
TRUSTED_V6_CHECK_APP_ID = 3518182
TRUSTED_V6_CHECK_APP_SLUG = "merglbot-pr-assistant-v4-stg"
TRUSTED_V6_CHECK_APP_OWNER = "merglbot-core"
V6_VALID_VERDICTS = {
    "approved_for_closeout",
    "changes_required",
    "blocked_missing_authority",
    "partial_authority",
    "review_generation_failed",
}
V6_VALID_STATUSES = {"success", "blocked", "failed", "degraded"}
# Markers that a valid v6 receipt provably never carries on the required
# check-run surface. Requiring any of them is a gate defect, not a merge blocker.
V6_NEVER_EMITTED_ON_CHECK_RUN = (
    "MERGLBOT_PR_CHECK_SURFACE",
    "MERGLBOT_FOLLOW_UP_ID",
    "MERGLBOT_DOCUMENTATION_OBLIGATION_STATE",
    "MERGLBOT_RUN_URL",
)


def gh_json(args: list[str]) -> Any:
    proc = subprocess.run(
        ["gh", *args],
        check=False,
        text=True,
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"gh {' '.join(args)} failed")
    return json.loads(proc.stdout)


def parse_markers(body: str) -> dict[str, str]:
    return {key.strip(): value.strip() for key, value in MARKER_RE.findall(body or "")}


def normalize_machine_token(value: str) -> str:
    normalized = re.sub(r"[\s-]+", "_", value.strip().lower())
    normalized = MACHINE_TOKEN_STRIP_RE.sub("", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return normalized


def normalize_heading(value: str) -> str:
    heading = re.sub(r"^[#\s]+", "", value.strip())
    heading = re.sub(r"[*_`\s]+", "", heading)
    return heading.lower()


def docs_state_blocks_closeout(verdict: str, docs_state: str) -> bool:
    return verdict == "approved_for_closeout" and docs_state in {"missing", "unknown"}


def extract_zaver_field(body: str, field_name: str) -> str:
    in_zaver = False
    in_code = False
    for raw_line in (body or "").splitlines():
        line = raw_line.strip()
        if line.startswith("```") or line.startswith("~~~"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if SECTION_HEADER_RE.match(line):
            heading = normalize_heading(line)
            if (
                not in_zaver
                and ZAVER_SECTION_HEADER_RE.match(line)
                and heading in ("zaver", "závěr")
            ):
                in_zaver = True
                in_code = False
                continue
            if in_zaver:
                break
            continue
        if not in_zaver:
            continue
        cleaned = re.sub(r"^[\s>\-*+]*", "", line)
        parts = cleaned.split(":", 1)
        field_key = (
            parts[0].replace("*", "").replace("_", "").replace("`", "").strip()
            if parts
            else ""
        )
        if len(parts) == 2 and field_key.lower() == field_name.lower():
            return normalize_machine_token(parts[1])
    return ""


def latest_receipt(
    comments: list[dict[str, Any]],
) -> tuple[dict[str, str] | None, str | None, str]:
    for comment in reversed(comments):
        user = comment.get("user")
        if not isinstance(user, dict):
            continue
        if user.get("login") != "github-actions[bot]" or user.get("type") != "Bot":
            continue
        body = str(comment.get("body") or "")
        if "<!-- MERGLBOT_PR_ASSISTANT_V3 -->" not in body:
            continue
        markers = parse_markers(body)
        return markers, str(comment.get("html_url") or comment.get("url") or ""), body
    return None, None, ""


def expected_run_url(pr_url: str, run_id: str) -> str:
    if "/pull/" not in pr_url:
        return ""
    return f"{pr_url.split('/pull/', 1)[0]}/actions/runs/{run_id}"


def v6_producer_rejection(run: dict[str, Any]) -> str:
    """Return a reason string when a check run's producer is not trusted.

    The check-run NAME is attacker-controllable: anything with `checks: write` on
    the repository (including the default `GITHUB_TOKEN` of any workflow) can
    publish a run called `Merglbot PR Assistant v6` carrying a forged marker
    block. Binding to the producing GitHub App identity is what makes the
    receipt trustworthy, so a missing or unexpected `app` fails closed.
    """
    app = run.get("app")
    if not isinstance(app, dict):
        return "missing_app"
    if app.get("id") != TRUSTED_V6_CHECK_APP_ID:
        return f"app_id={app.get('id')!r}"
    if str(app.get("slug") or "") != TRUSTED_V6_CHECK_APP_SLUG:
        return f"app_slug={app.get('slug')!r}"
    owner = app.get("owner")
    owner_login = str((owner or {}).get("login") or "") if isinstance(owner, dict) else ""
    if owner_login != TRUSTED_V6_CHECK_APP_OWNER:
        return f"app_owner={owner_login!r}"
    return ""


def select_v6_check_run(
    check_runs: list[dict[str, Any]],
    *, repo: str = "", pr_number: int = 0,
) -> tuple[dict[str, Any] | None, bool, list[str]]:
    """Pick the newest COMPLETED v6 check run from the TRUSTED producer.

    Returns `(selected, pending, untrusted_reasons)`.

    Selection follows MERGLBOT_PR_ASSISTANT_V6.md section 8: newest completed run
    wins, tie-broken by the larger check-run id, and ANY queued/in-progress v6 run
    on the head means a consumer must WAIT rather than read a stale verdict.

    Runs that carry the v6 check NAME but were produced by an untrusted identity
    are never selectable — so a forged run can neither be read nor shadow the
    genuine one — and their reasons are returned so the caller can fail closed
    rather than silently ignore them.
    """
    named = [
        run
        for run in check_runs
        if isinstance(run, dict) and str(run.get("name") or "") == V6_CHECK_NAME
    ]
    trusted: list[dict[str, Any]] = []
    untrusted_reasons: list[str] = []
    for run in named:
        rejection = v6_producer_rejection(run)
        if rejection:
            untrusted_reasons.append(rejection)
        else:
            trusted.append(run)
    pending = any(str(run.get("status") or "") != "completed" for run in trusted)
    completed = [run for run in trusted if str(run.get("status") or "") == "completed"]
    if repo and pr_number:
        # Commit-scoped siblings must not shadow this PR's bound receipt.
        # With no matching receipt, retain the newest one so binding failures
        # remain explicit. Untrusted publishers and pending runs stay global.
        bound = [run for run in completed if parse_markers(
            str((run.get("output") or {}).get("summary") or "")
        ).get("MERGLBOT_REVIEW_SOURCE") == expected_review_source(repo, pr_number)]
        if bound:
            completed = bound
    if not completed:
        return None, pending, untrusted_reasons
    newest = max(
        completed,
        key=lambda run: (str(run.get("completed_at") or ""), int(run.get("id") or 0)),
    )
    return newest, pending, untrusted_reasons


def findings_count_blocks(actionable_findings_count: str) -> bool:
    """True when the count reports REAL findings (leading 1-9).

    This is the keeper's `^[1-9]` discriminator and it distinguishes "the review
    found things" from "the count was not delivered". It is deliberately NOT the
    whole gate — see `findings_count_is_delivered_zero`.
    """
    return bool(BLOCKING_FINDINGS_COUNT_RE.match(actionable_findings_count.strip()))


def findings_count_is_delivered_zero(actionable_findings_count: str) -> bool:
    """True only for an explicit `0`.

    `unknown` (and an absent marker) mean the findings payload was never
    delivered, NOT that there were none, so they can never be merge evidence.
    The keeper passes them today; this verifier is the stricter closeout gate
    required by PR_POLICY section 3.5.1, so it denies them.
    """
    return actionable_findings_count.strip() == "0"


def expected_review_source(repo: str, pr_number: int) -> str:
    """The `MERGLBOT_REVIEW_SOURCE` value a receipt for this PR must carry."""
    return f"{repo}#{pr_number}"


def run_lease_id(run_id: str) -> str:
    """The lease id shared by `MERGLBOT_RUN_ID` and `MERGLBOT_REVIEW_RUN_ID`.

    The two markers carry the same producing run under different prefixes —
    measured on five live v6 check-run receipts, `MERGLBOT_RUN_ID` is
    `pr-assistant-v6:local-primary:<lease_id>` while `MERGLBOT_REVIEW_RUN_ID` is
    `local-primary:<lease_id>`. Only the final segment is compared: the prefix is
    runtime/lane-dependent (see `pr-assistant/receipt-verification.md`), so
    hard-coding one would break the next runtime.
    """
    return run_id.strip().rsplit(":", 1)[-1]


#: The ONLY statuses that count as a produced verdict. An ALLOWLIST, deliberately:
#: a denylist of known no-verdict statuses (`skipped`/`error`/`timeout`/`missing`)
#: passes anything it has not heard of — `codex:cancelled`, `codex:pending`, or a
#: colon-less `garbled` — and platform#1039 happened precisely because an
#: unanticipated worker shape slipped past a gate. In merge evidence the unknown
#: direction must be closed, not open.
ENGINE_PRODUCED_VERDICT_STATUSES = frozenset({"pass", "fail"})


def engine_evidence_has_produced_verdict(engine_evidence: str) -> bool:
    """True when at least one engine produced a verdict (`pass`/`fail`).

    Fail-closed on an ABSENT, unreadable or unrecognised marker: nothing proves a
    review happened, so an empty string — or evidence made only of statuses this
    verifier does not recognise — returns False.

    This exists because `MERGLBOT_PROVIDER_DEGRADED: false` does NOT prove a
    review happened. Measured 2026-08-20 on merglbot-core/platform#1039 head
    `3f8d3dd8`: `codex:skipped,claude:skipped` published together with
    `PROVIDER_DEGRADED: false`, `DEGRADED_REASON: none` and a delivered
    `ACTIONABLE_FINDINGS_COUNT: 0`, so every other check in
    `evaluate_v6_receipt` passed on a round in which no engine reviewed
    anything. Root cause is cross-component (merglbot-core/platform#1066): the
    worker omits its coverage flag on all-skipped branches assuming the gate
    infers degradation from evidence, and the gate infers only when NO review
    payload arrived at all.

    A produced ``fail`` counts as evidence a review HAPPENED — conflating it
    with an absence would route a real negative verdict into the no-coverage
    lane. It costs nothing to admit it here: a fail-verdict receipt is already
    denied by the status/verdict checks in `evaluate_v6_receipt`.
    """

    entries = [entry.strip() for entry in engine_evidence.split(",") if entry.strip()]
    for entry in entries:
        # The engine NAME folds; the STATUS does not. The docs define this gate as "an
        # allowlist of exactly `pass` and `fail`" where any unrecognised status does not
        # count, and the trusted builder emits lowercase — so `codex:PASS` can only come
        # from a builder bug, which is what the engine-name check below already hardens
        # against. Folding it accepted the malformed shape in the FAIL-OPEN direction
        # (counted as review coverage), widening the gate past its own spec.
        #
        # 🔴 `engine_evidence_has_produced_fail` deliberately does the OPPOSITE and stays
        # case-INSENSITIVE: there, accepting a case variant means a `FAIL` under an
        # approval still trips the contradiction blocker, which is fail-CLOSED. The two
        # predicates want opposite leniency because their failure directions are
        # opposite; keep them that way.
        engine, separator, status = entry.partition(":")
        engine = engine.lower()
        if not separator:
            continue
        # The engine NAME must be present too. Discarding it let a malformed
        # `:pass` — no engine at all — count as a produced verdict. The marker
        # comes from the trusted deterministic builder, so this is hardening
        # against a builder bug rather than a reachable trust-boundary hole,
        # but it costs one condition and matches the fail-closed rule above.
        if not engine.strip():
            continue
        if status.strip() in ENGINE_PRODUCED_VERDICT_STATUSES:
            return True
    return False


def engine_evidence_has_produced_fail(engine_evidence: str) -> bool:
    """True when some engine produced a `fail` verdict.

    Deliberately NOT the negation of
    :func:`engine_evidence_has_produced_verdict`: a receipt can carry both a
    `pass` and a `fail`, and this asks a different question — is there a
    negative signal inside a receipt whose top-level verdict claims approval?
    Unreadable and unrecognised entries answer False here, because asserting a
    contradiction needs positive evidence of one.
    """

    for entry in engine_evidence.split(","):
        engine, separator, status = entry.strip().lower().partition(":")
        if separator and engine.strip() and status.strip() == "fail":
            return True
    return False


def run_identity_blockers(markers: dict[str, str]) -> list[str]:
    """Blockers for an inconsistent or unidentified producing run.

    Both markers are emitted by the live v6 check-run lane (present on 5/5
    receipts measured), so this lane owes `MERGLBOT_REVIEW_RUN_ID` and its
    absence is a malformed receipt — not a lane difference. Two markers naming
    different runs means the receipt does not identify one unambiguous producing
    run, which is the whole point of carrying them.
    """
    blockers: list[str] = []
    run_id = markers.get("MERGLBOT_RUN_ID", "")
    review_run_id = markers.get("MERGLBOT_REVIEW_RUN_ID", "")
    if not review_run_id:
        blockers.append("missing_review_run_identity")
    elif run_id and run_lease_id(run_id) != run_lease_id(review_run_id):
        blockers.append("review_run_id_mismatch")
    return blockers


V6_CARRIED_FORWARD_MODEL = "carried-forward"


def receipt_is_carried_forward(markers: dict[str, str]) -> bool:
    """True when the receipt carries a prior clean verdict instead of an engine run on this head.

    Either signal counts (docs#1397 review round 3): the synthesis model marker
    `MERGLBOT_REVIEW_MODEL: carried-forward`, or any engine row of
    `MERGLBOT_LOCAL_PRIMARY_ENGINE_MODELS` naming `carried-forward` — a carried
    row never ran on this head whatever the synthesis label says.
    """
    if markers.get("MERGLBOT_REVIEW_MODEL", "") == V6_CARRIED_FORWARD_MODEL:
        return True
    engine_models = markers.get("MERGLBOT_LOCAL_PRIMARY_ENGINE_MODELS", "")
    return any(
        row.split(":", 1)[1].strip() == V6_CARRIED_FORWARD_MODEL
        for row in engine_models.split(",")
        if ":" in row
    )


CARRIED_FROM_MARKER_RE = re.compile(
    r"<!--\s*MERGLBOT_REVIEW_CARRIED_FROM:\s*([0-9a-f]{7,40})\s*-->", re.IGNORECASE
)
CARRIED_DIFF_MARKER_RE = re.compile(
    r"<!--\s*MERGLBOT_REVIEW_DIFF_SHA256:\s*([0-9a-f]{16,64})\s*-->", re.IGNORECASE
)


def carried_forward_source(bodies: list[str], head_sha: str = "") -> dict[str, str | None]:
    """The source head and diff hash a carried receipt names.

    The gate summary carries only `MERGLBOT_REVIEW_MODEL: carried-forward`; the
    prior head and the diff hash live in the review body the worker publishes
    (`MERGLBOT_REVIEW_CARRIED_FROM`, `MERGLBOT_REVIEW_DIFF_SHA256`). Informative
    only — the verdict binding stays on the check-run head marker. A body that
    names the live head (`head_sha`) wins over any other; otherwise the newest
    carried body is reported, so stale provenance is possible and the fields
    must never feed a decision.
    """
    carried_from = None
    diff_sha256 = None
    bound = False
    for body in bodies:
        text = body or ""
        m = CARRIED_FROM_MARKER_RE.search(text)
        if not m:
            continue
        names_head = bool(head_sha) and head_sha.lower() in text.lower()
        if bound and not names_head:
            continue
        carried_from = m.group(1).lower()
        d = CARRIED_DIFF_MARKER_RE.search(text)
        diff_sha256 = d.group(1).lower() if d else None
        bound = bound or names_head
    return {"carried_from": carried_from, "diff_sha256": diff_sha256}


def evaluate_v6_receipt(
    markers: dict[str, str],
    conclusion: str,
    head_sha: str,
    repo: str = "",
    pr_number: int = 0,
    linked_pull_requests: list[int] | None = None,
    require_verified_engine_run: bool = False,
) -> list[str]:
    """Blockers for a v6 check-run receipt.

    A CARRIED-FORWARD receipt (`MERGLBOT_REVIEW_MODEL: carried-forward`,
    platform#1693 / docs#1383) is current-head evidence like any other
    approval: the gate publishes it on the live head after the worker proved
    the PR's own diff byte-identical to an approved one and main's changes
    non-overlapping (PR_POLICY.md section 3.4.1). Its engine rows say `pass`
    because the carried verdict IS both engines' pass, but no engine ran on
    this head, so a consumer that needs a VERIFIED engine run on this exact
    head (dual-engine authority lane, manual-merge coverage) passes
    `require_verified_engine_run=True` and gets `carried_forward_not_verified_engine_run`.

    Deliberately does NOT require MERGLBOT_PR_CHECK_SURFACE, MERGLBOT_RUN_URL,
    MERGLBOT_FOLLOW_UP_ID or MERGLBOT_DOCUMENTATION_OBLIGATION_STATE: the v6
    check-run receipt never carries them, so requiring them would reject every
    valid v6 receipt.

    Check runs are COMMIT-scoped, not PR-scoped, so the head SHA alone does not
    identify which PR was reviewed. When one commit is the head of two PRs
    (stacked PRs, a duplicated branch, a re-open, a retarget) the bases — and
    therefore the reviewed diffs — differ, so an approval published for PR A
    must never satisfy PR B. The receipt is bound to its PR through
    `MERGLBOT_REVIEW_SOURCE`, which the trusted producing app writes into the
    summary (its trustworthiness rests on the app-identity binding in
    `select_v6_check_run`).

    `linked_pull_requests` is the Checks API `check_runs[].pull_requests` list.
    It CORROBORATES the marker but can never replace it: measured live, GitHub
    populates it only while a PR is open and returns `[]` for every merged PR,
    so requiring it would fail-close every closed-PR verification. A non-empty
    list that omits this PR is a real contradiction and blocks.
    """
    blockers: list[str] = []
    review_head_sha = markers.get("MERGLBOT_REVIEW_HEAD_SHA", "")
    if not (head_sha and review_head_sha and head_sha == review_head_sha):
        blockers.append("merglbot_review_head_sha_mismatch")
    if repo and pr_number:
        review_source = markers.get("MERGLBOT_REVIEW_SOURCE", "")
        if not review_source:
            blockers.append("missing_review_source_binding")
        elif review_source != expected_review_source(repo, pr_number):
            blockers.append(f"review_source_pr_mismatch:{review_source}")
        if linked_pull_requests and pr_number not in linked_pull_requests:
            blockers.append("check_run_not_linked_to_pr")
    if markers.get("MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION", "") != "1":
        blockers.append("unsupported_or_missing_receipt_schema")
    if markers.get("MERGLBOT_MARKER_STATUS", "") != "parseable_receipt":
        blockers.append("receipt_not_parseable")
    status = markers.get("MERGLBOT_REVIEW_STATUS", "")
    if status not in V6_VALID_STATUSES:
        blockers.append("missing_or_invalid_review_status")
    verdict = markers.get("MERGLBOT_REVIEW_VERDICT", "")
    if verdict not in V6_VALID_VERDICTS:
        blockers.append("missing_or_invalid_review_verdict")
    # Fail-closed: an absent degradation marker is read as degraded.
    if markers.get("MERGLBOT_PROVIDER_DEGRADED", "") != "false":
        blockers.append("provider_degraded_or_missing")
    # `PROVIDER_DEGRADED: false` does not prove a review happened, so the
    # degradation check above cannot stand in for this one — see
    # `engine_evidence_has_produced_verdict`. Fail-closed on an absent marker.
    engine_evidence = markers.get("MERGLBOT_LOCAL_PRIMARY_ENGINE_EVIDENCE", "")
    if not engine_evidence_has_produced_verdict(engine_evidence):
        blockers.append("no_engine_produced_verdict")
    # Internal-consistency cross-check. An accepted receipt claims a clean review;
    # a produced engine `fail` inside it contradicts that claim on a second axis.
    # Reaching this state needs the trusted builder to be wrong about BOTH the
    # top-level verdict and the conclusion (a produced fail publishes `failure`,
    # already denied below), so it is builder-bug hardening, not a reachable
    # trust-boundary hole.
    #
    # Measured before adding it, because an earlier review round argued the
    # opposite — that approval over an engine `fail` is the normal outcome of a
    # refuted engine finding and blocking it would fail-close valid approvals.
    # Across 40 `approved_for_closeout` receipts on merged PR heads in
    # platform / docs / infra / agents-orchestrator, ZERO carried a produced
    # `fail`; the round that made the argument published `changes_required`, not
    # an approval. And the direction is safe either way: this blocker only ever
    # withholds merge EVIDENCE, so a false positive costs an investigation, while
    # its absence costs a merge over a contradicted receipt.
    if verdict == "approved_for_closeout" and engine_evidence_has_produced_fail(engine_evidence):
        blockers.append("produced_fail_contradicts_approval")
    if require_verified_engine_run and receipt_is_carried_forward(markers):
        blockers.append("carried_forward_not_verified_engine_run")
    findings_count = markers.get("MERGLBOT_ACTIONABLE_FINDINGS_COUNT", "")
    if findings_count_blocks(findings_count):
        blockers.append("actionable_findings_require_fix")
    elif not findings_count_is_delivered_zero(findings_count):
        # `unknown` / absent / malformed: the findings list was not delivered, so
        # nothing proves there are none.
        blockers.append("actionable_findings_count_not_delivered")
    if not markers.get("MERGLBOT_RUN_ID", ""):
        blockers.append("missing_review_run_id")
    blockers.extend(run_identity_blockers(markers))
    if status != "success" or verdict != "approved_for_closeout":
        blockers.append("review_not_approved_for_closeout")
    if conclusion != "success":
        blockers.append("v6_check_run_not_success")
    # A reported docs state still blocks; an ABSENT marker does not, because the
    # v6 check-run receipt never carries one (see PR_POLICY.md section 3.6).
    docs_state = markers.get("MERGLBOT_DOCUMENTATION_OBLIGATION_STATE", "")
    if docs_state and docs_state_blocks_closeout(verdict, docs_state):
        blockers.append("review_docs_state_blocks_closeout")
    return blockers


def flatten_check_run_pages(pages: Any) -> list[dict[str, Any]]:
    """Flatten `gh api --paginate --slurp` check-run pages into one list."""
    runs: list[dict[str, Any]] = []
    for page in pages if isinstance(pages, list) else [pages]:
        if not isinstance(page, dict):
            continue
        for run in page.get("check_runs") or []:
            if isinstance(run, dict):
                runs.append(run)
    return runs


def verify_v6(
    repo: str,
    pr: dict[str, Any],
    head_sha: str,
    pr_number: int,
    *,
    read_json: Callable[[list[str]], Any] | None = None,
    require_verified_engine_run: bool = False,
) -> dict[str, Any] | None:
    """Verify through the v6 check-run surface, or return None if absent."""
    # docs#1383 review round 2: an injected transport is a canonical-read adapter
    # (the docs-maintenance client halts itself on any read it does not
    # support), so the optional provenance lookup below runs ONLY on the
    # default `gh` transport; with an injected one the informative fields stay
    # null and no extra read is issued.
    provenance_reader = gh_json if read_json is None else None
    read_json = gh_json if read_json is None else read_json
    # `filter=all` is REQUIRED, not a nicety. The endpoint defaults to
    # `filter=latest`, which returns only the most recent run PER CHECK NAME and
    # therefore hides superseded and still-running siblings. Measured live on
    # merglbot-public/docs: heads 5ce59b88, 75ab75b6, c7ed98c4 and fff81430 each
    # carry TWO `Merglbot PR Assistant v6` runs, and the default view shows one
    # — on 75ab75b6 it shows only the later `success` and hides the earlier
    # `failure`. That blinds the WAIT rule to exactly the state it exists for: a
    # queued or in-progress run sitting behind a newer completed one.
    #
    # Paginated for the same reason: a head can carry more than 100 check runs,
    # and reading only the first page can hide the trusted or pending v6 run.
    # `verify_v3` already paginates; this mirrors it.
    check_runs = flatten_check_run_pages(
        read_json(
            [
                "api",
                "--paginate",
                "--slurp",
                f"repos/{repo}/commits/{head_sha}/check-runs"
                "?per_page=100&filter=all",
            ]
        )
    )
    selected, pending, untrusted_reasons = select_v6_check_run(check_runs, repo=repo, pr_number=pr_number)
    # Only "no v6 check run of any kind" may fall through to the v3 comment
    # surface. A run that claimed the v6 name from an untrusted producer must
    # fail closed here, never be silently ignored.
    if selected is None and not pending and not untrusted_reasons:
        return None

    blockers: list[str] = []
    markers: dict[str, str] = {}
    conclusion = ""
    check_url = None
    linked_pull_requests: list[int] = []
    for reason in untrusted_reasons:
        blockers.append(f"v6_check_run_untrusted_producer:{reason}")
    if pending:
        blockers.append("v6_review_in_progress")
    if selected is None:
        blockers.append("missing_merglbot_review_receipt")
    else:
        output = selected.get("output")
        summary = str((output or {}).get("summary") or "")
        markers = parse_markers(summary)
        conclusion = str(selected.get("conclusion") or "")
        check_url = str(selected.get("html_url") or selected.get("url") or "") or None
        linked_pull_requests = [
            int(item["number"])
            for item in (selected.get("pull_requests") or [])
            if isinstance(item, dict) and isinstance(item.get("number"), int)
        ]
        if not markers:
            blockers.append("missing_merglbot_review_receipt")
        else:
            blockers.extend(
                evaluate_v6_receipt(
                    markers,
                    conclusion,
                    head_sha,
                    repo=repo,
                    pr_number=pr_number,
                    linked_pull_requests=linked_pull_requests,
                    require_verified_engine_run=require_verified_engine_run,
                )
            )

    carried = bool(markers) and receipt_is_carried_forward(markers)
    carried_source: dict[str, str | None] = {"carried_from": None, "diff_sha256": None}
    if carried and provenance_reader is not None:
        # Best-effort, read-only, default transport only: the review body that
        # names the source head.
        try:
            pages = provenance_reader(
                [
                    "api",
                    "--paginate",
                    "--slurp",
                    f"repos/{repo}/issues/{pr_number}/comments?per_page=100",
                ]
            )
            comments: list[Any] = []
            for page in pages if isinstance(pages, list) else []:
                comments.extend(page if isinstance(page, list) else [page])
            bodies = [str(c.get("body") or "") for c in comments if isinstance(c, dict)]
        except Exception:  # noqa: BLE001 - informative field only
            bodies = []
        carried_source = carried_forward_source(bodies, head_sha)

    return {
        "ok": len(blockers) == 0,
        "repo": repo,
        "pr_number": int(pr.get("number") or 0) or None,
        "pr_url": pr.get("url"),
        "receipt_era": "v6",
        "receipt_surface": markers.get("MERGLBOT_RECEIPT_SURFACE")
        or "check_run_summary",
        "head_sha": head_sha or None,
        "review_head_sha": markers.get("MERGLBOT_REVIEW_HEAD_SHA") or None,
        "current_head_match": bool(
            head_sha and markers.get("MERGLBOT_REVIEW_HEAD_SHA") == head_sha
        ),
        "schema_version": markers.get("MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION") or None,
        "verdict": markers.get("MERGLBOT_REVIEW_VERDICT") or None,
        "status": markers.get("MERGLBOT_REVIEW_STATUS") or None,
        "provider_degraded": markers.get("MERGLBOT_PROVIDER_DEGRADED") or None,
        "actionable_findings_count": markers.get("MERGLBOT_ACTIONABLE_FINDINGS_COUNT")
        or None,
        # Absent by contract on the v6 check-run surface; reported as null for
        # auditability, never required.
        "documentation_obligation_state": markers.get(
            "MERGLBOT_DOCUMENTATION_OBLIGATION_STATE"
        )
        or None,
        # Informative, NEVER a blocker (PR_POLICY.md section 3.6): absence of the
        # docs-state marker is the v6 norm, so it cannot fail the receipt — but a
        # closeout consumer that sees `ok: true` still has to resolve the docs
        # obligation from same-PR evidence, and nothing else in this payload says
        # so. This flag is that explicit signal.
        "docs_obligation_requires_external_evidence": (
            selected is not None
            and bool(markers)
            and not markers.get("MERGLBOT_DOCUMENTATION_OBLIGATION_STATE")
        ),
        "review_source": markers.get("MERGLBOT_REVIEW_SOURCE") or None,
        "autonomous_next_action": markers.get("MERGLBOT_AUTONOMOUS_NEXT_ACTION") or None,
        "local_primary_engine_evidence": markers.get("MERGLBOT_LOCAL_PRIMARY_ENGINE_EVIDENCE") or None,
        # docs#1383: a carried receipt is current-head evidence (PR_POLICY.md
        # section 3.4.1); these fields say so and name its source head. They
        # block only under --require-verified-engine-run.
        "review_model": markers.get("MERGLBOT_REVIEW_MODEL") or None,
        "carried_forward": carried,
        "carried_from": carried_source["carried_from"],
        "carried_diff_sha256": carried_source["diff_sha256"],
        "expected_review_source": expected_review_source(repo, pr_number),
        "linked_pull_requests": linked_pull_requests,
        "check_run_conclusion": conclusion or None,
        "check_run_url": check_url,
        "check_run_app_slug": (
            str(((selected or {}).get("app") or {}).get("slug") or "") or None
        ),
        "untrusted_producer_runs": len(untrusted_reasons),
        "run_id": markers.get("MERGLBOT_RUN_ID") or None,
        "review_run_id": markers.get("MERGLBOT_REVIEW_RUN_ID") or None,
        "blockers": blockers,
    }


def v3_run_provenance(
    run_id: str,
    lookup_run: Any,
) -> tuple[list[str], str]:
    """Prove WHICH workflow produced a v3 comment receipt.

    A v3 receipt is published by a GitHub Actions workflow, so its run id is
    ALWAYS a numeric `workflow_run.id` resolvable through the Actions API. The
    SURFACE decides this rule, not the shape of the id: opaque ids (the v6
    `pr-assistant-v6:...` shape) are legitimate only on the v6 check-run surface,
    which never reaches this function.

    A non-numeric id here therefore means provenance cannot be proven, and that
    must BLOCK. Merely skipping the Actions lookup (as an earlier revision did)
    is fail-open: `expected_run_url` is constructible for any id string, so a
    forged `github-actions[bot]` comment carrying a non-numeric run id would
    reach `ok=true` having never proven which workflow produced it.

    `lookup_run` is injected so the rule is testable without network access.
    """
    if not run_id:
        # The caller already reports `missing_review_run_id`.
        return [], ""
    if not run_id.isdigit():
        return ["invalid_v3_run_id"], ""
    blockers: list[str] = []
    run_path = ""
    lookup_failed = False
    try:
        run = lookup_run(run_id)
        run_path = str((run or {}).get("path") or "")
    except Exception as exc:
        lookup_failed = True
        blockers.append(f"review_run_lookup_failed:{exc}")
    if not lookup_failed:
        if not run_path:
            # A successful lookup that yields no workflow path proves nothing
            # about which workflow published the receipt, which is this
            # function's whole contract. Passing it would be fail-open.
            blockers.append("review_run_path_unknown")
        elif run_path not in PR_ASSISTANT_WORKFLOW_PATHS:
            blockers.append("review_run_not_from_pr_assistant_workflow")
    return blockers, run_path


def verify(
    repo: str,
    pr_number: int,
    surface: str = "auto",
    allow_v3_fallback: bool = False,
    *,
    read_json: Callable[[list[str]], Any] | None = None,
    require_verified_engine_run: bool = False,
) -> dict[str, Any]:
    # Optional transport is a new read-only v6 adapter contract, not a verdict hook.
    if read_json is not None and (surface != "v6" or allow_v3_fallback):
        raise ValueError("injected_transport_requires_explicit_v6")
    injected = read_json is not None
    read_json = gh_json if read_json is None else read_json
    pr = read_json(
        ["pr", "view", str(pr_number), "--repo", repo, "--json", "headRefOid,url,state"]
    )
    pr["number"] = pr_number
    head_sha = str(pr.get("headRefOid") or "")

    if surface in {"auto", "v6"}:
        v6_result = verify_v6(
            repo,
            pr,
            head_sha,
            pr_number,
            read_json=read_json if injected else None,
            require_verified_engine_run=require_verified_engine_run,
        )
        if v6_result is not None:
            return v6_result
        # No v6 check run was observed. That is NOT proof the gate is inactive,
        # and the v3 comment surface has a categorically weaker trust root (any
        # workflow `GITHUB_TOKEN` can author a `github-actions[bot]` comment).
        # Silently downgrading would let a transient blind spot route an
        # active-gate PR onto a forgeable surface, so `auto` fails closed unless
        # the caller explicitly opts into the historical surface.
        if surface == "v6" or not allow_v3_fallback:
            return {
                "ok": False,
                "repo": repo,
                "pr_number": pr_number,
                "pr_url": pr.get("url"),
                "receipt_era": "v6",
                "receipt_surface": "check_run_summary",
                "head_sha": head_sha or None,
                "expected_review_source": expected_review_source(repo, pr_number),
                "blockers": ["missing_merglbot_review_receipt"],
            }

    return verify_v3(repo, pr_number, pr, head_sha)


def verify_v3(
    repo: str,
    pr_number: int,
    pr: dict[str, Any],
    head_sha: str,
) -> dict[str, Any]:
    comments_pages = gh_json(
        [
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repo}/issues/{pr_number}/comments?per_page=100",
        ]
    )
    comments = [
        item
        for page in (comments_pages if isinstance(comments_pages, list) else [])
        for item in (page if isinstance(page, list) else [page])
    ]
    markers, comment_url, receipt_body = latest_receipt(comments)

    blockers: list[str] = []
    if not markers:
        blockers.append("missing_merglbot_review_receipt")
        markers = {}

    review_head_sha = markers.get("MERGLBOT_REVIEW_HEAD_SHA", "")
    verdict = markers.get("MERGLBOT_REVIEW_VERDICT", "")
    status = markers.get("MERGLBOT_REVIEW_STATUS", "")
    schema_version = markers.get("MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION", "")
    pr_check_surface = markers.get("MERGLBOT_PR_CHECK_SURFACE", "")
    run_id = markers.get("MERGLBOT_RUN_ID", "")
    run_url = markers.get("MERGLBOT_RUN_URL", "")
    docs_state_marker_present = "MERGLBOT_DOCUMENTATION_OBLIGATION_STATE" in markers
    docs_state = markers.get("MERGLBOT_DOCUMENTATION_OBLIGATION_STATE", "")

    current_head_match = bool(
        head_sha and review_head_sha and head_sha == review_head_sha
    )
    if not current_head_match:
        blockers.append("merglbot_review_head_sha_mismatch")
    if schema_version != "1":
        blockers.append("unsupported_or_missing_receipt_schema")
    if status not in {"success", "blocked", "failed"}:
        blockers.append("missing_or_invalid_review_status")
    valid_verdicts = {
        "approved_for_closeout",
        "changes_required",
        "blocked_missing_authority",
        "review_generation_failed",
    }
    if verdict not in valid_verdicts:
        blockers.append("missing_or_invalid_review_verdict")
    visible_verdict = extract_zaver_field(receipt_body, "Verdict")
    if visible_verdict in valid_verdicts and verdict and visible_verdict != verdict:
        blockers.append("review_visible_verdict_marker_mismatch")
    valid_docs_states = {"satisfied", "not_required", "missing", "unknown"}
    if not docs_state_marker_present:
        docs_state = "unknown"
        blockers.append("missing_or_invalid_documentation_obligation_state")
    elif docs_state not in valid_docs_states:
        blockers.append("missing_or_invalid_documentation_obligation_state")
    visible_docs_state = extract_zaver_field(receipt_body, "Documentation Obligation State")
    if visible_docs_state in valid_docs_states and docs_state and visible_docs_state != docs_state:
        blockers.append("review_visible_docs_state_marker_mismatch")
    if docs_state_blocks_closeout(verdict, docs_state):
        blockers.append("review_docs_state_blocks_closeout")
    if status != "success" or verdict != "approved_for_closeout":
        blockers.append("review_not_approved_for_closeout")
    if status == "success" and verdict != "approved_for_closeout":
        blockers.append("review_status_verdict_mismatch")
    if status == "failed" and verdict != "review_generation_failed":
        blockers.append("review_status_verdict_mismatch")
    if pr_check_surface != "verified":
        blockers.append("pr_check_surface_not_verified")
    if not run_id:
        blockers.append("missing_review_run_id")
    if not run_url:
        blockers.append("missing_review_run_url")
    elif run_id and run_url != expected_run_url(str(pr.get("url") or ""), run_id):
        blockers.append("review_run_url_mismatch")
    provenance_blockers, run_path = v3_run_provenance(
        run_id,
        lambda rid: gh_json(["api", f"repos/{repo}/actions/runs/{rid}"]),
    )
    blockers.extend(provenance_blockers)

    return {
        "ok": len(blockers) == 0,
        "repo": repo,
        "pr_number": pr_number,
        "pr_url": pr.get("url"),
        "receipt_era": "v3",
        "receipt_surface": "pr_comment",
        "head_sha": head_sha or None,
        "review_head_sha": review_head_sha or None,
        "current_head_match": current_head_match,
        "schema_version": schema_version or None,
        "verdict": verdict or None,
        "status": status or None,
        "documentation_obligation_state": docs_state or None,
        "pr_check_surface": pr_check_surface or None,
        "comment_url": comment_url,
        "run_id": run_id or None,
        "run_url": run_url or None,
        "run_path": run_path or None,
        "blockers": blockers,
    }


FIXTURE_DIR = Path(__file__).resolve().parents[2] / "projects" / "ai-efficiency-2026-09" / "fixtures" / "1383"


def run_fixture_cases(fixture_dir: Path = FIXTURE_DIR) -> bool:
    """Replay the docs#1383 receipt fixtures: each file holds the check-run
    summary, the live head, the consumer flag and the expected blockers."""
    if not fixture_dir.is_dir():
        raise AssertionError(f"fixture dir missing: {fixture_dir}")
    files = sorted(fixture_dir.glob("*.json"))
    if not files:
        raise AssertionError(f"no fixtures in {fixture_dir}")
    for path in files:
        case = json.loads(path.read_text(encoding="utf-8"))
        got = evaluate_v6_receipt(
            parse_markers(case["summary"]),
            case.get("conclusion", "success"),
            case["head_sha"],
            require_verified_engine_run=bool(case.get("require_verified_engine_run", False)),
        )
        if got != case["expected_blockers"]:
            raise AssertionError(f"{path.name}: expected {case['expected_blockers']}, got {got}")
    return True


def self_test() -> int:
    body = "\n".join(
        [
            "<!-- MERGLBOT_PR_ASSISTANT_V3 -->",
            "<!-- MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION: 1 -->",
            "<!-- MERGLBOT_REVIEW_HEAD_SHA: abc123 -->",
            "<!-- MERGLBOT_REVIEW_VERDICT: approved_for_closeout -->",
            "<!-- MERGLBOT_REVIEW_STATUS: success -->",
            "<!-- MERGLBOT_DOCUMENTATION_OBLIGATION_STATE: not_required -->",
            "<!-- MERGLBOT_PR_CHECK_SURFACE: verified -->",
            "<!-- MERGLBOT_RUN_ID: 42 -->",
            "<!-- MERGLBOT_RUN_URL: https://github.com/o/r/actions/runs/42 -->",
        ]
    )
    markers = parse_markers(body)
    assert markers["MERGLBOT_REVIEW_HEAD_SHA"] == "abc123"
    assert markers["MERGLBOT_REVIEW_STATUS"] == "success"
    assert markers["MERGLBOT_RUN_ID"] == "42"
    trusted_markers, _, trusted_body = latest_receipt(
        [{"body": body, "user": {"login": "github-actions[bot]", "type": "Bot"}}]
    )
    assert trusted_markers and trusted_markers["MERGLBOT_REVIEW_HEAD_SHA"] == "abc123"
    assert extract_zaver_field(trusted_body, "Verdict") == ""
    assert normalize_machine_token("Review V4 Failed!") == "review_v4_failed"
    assert normalize_machine_token("approved-for-closeout") == "approved_for_closeout"
    assert normalize_machine_token("approved\tfor\ncloseout") == "approved_for_closeout"
    assert docs_state_blocks_closeout("approved_for_closeout", "missing")
    assert docs_state_blocks_closeout("approved_for_closeout", "unknown")
    assert not docs_state_blocks_closeout("approved_for_closeout", "not_required")
    assert not docs_state_blocks_closeout("changes_required", "unknown")
    assert (
        extract_zaver_field("## Zaver\n_Verdict_: approved-for-closeout", "Verdict")
        == "approved_for_closeout"
    )
    assert extract_zaver_field("### Zaver\n* _Verdict_ : approved-for-closeout", "Verdict") == ""
    assert (
        extract_zaver_field(
            "## Zaver\n### Details\nVerdict: approved_for_closeout",
            "Verdict",
        )
        == ""
    )
    assert (
        extract_zaver_field(
            "\n".join(
                [
                    "```markdown",
                    "## Zaver",
                    "Verdict: changes_required",
                    "```",
                    "## Zaver",
                    "```",
                    "## Spoofed",
                    "Verdict: changes_required",
                    "```",
                    "Verdict: approved_for_closeout",
                ]
            ),
            "Verdict",
        )
        == "approved_for_closeout"
    )
    assert (
        extract_zaver_field(
            "\n".join(
                [
                    "~~~markdown",
                    "## Zaver",
                    "Verdict: changes_required",
                    "~~~",
                    "## Zaver",
                    "Verdict: approved_for_closeout",
                ]
            ),
            "Verdict",
        )
        == "approved_for_closeout"
    )
    spoofed_markers, _, _ = latest_receipt(
        [{"body": body, "user": {"login": "octocat", "type": "User"}}]
    )
    assert spoofed_markers is None
    mismatched_body = "\n".join(
        [
            "## **Zaver**",
            "Verdict: approved_for_closeout",
            "",
            "<!-- MERGLBOT_PR_ASSISTANT_V3 -->",
            "<!-- MERGLBOT_REVIEW_VERDICT: blocked_missing_authority -->",
            "<!-- MERGLBOT_DOCUMENTATION_OBLIGATION_STATE: unknown -->",
        ]
    )
    assert extract_zaver_field(mismatched_body, "Verdict") == "approved_for_closeout"
    mismatched_markers = parse_markers(mismatched_body)
    assert mismatched_markers["MERGLBOT_REVIEW_VERDICT"] != extract_zaver_field(
        mismatched_body,
        "Verdict",
    )
    failed = parse_markers(
        "\n".join(
            [
                "<!-- MERGLBOT_REVIEW_VERDICT: review_generation_failed -->",
                "<!-- MERGLBOT_REVIEW_STATUS: failed -->",
            ]
        )
    )
    assert failed["MERGLBOT_REVIEW_VERDICT"] == "review_generation_failed"
    assert failed["MERGLBOT_REVIEW_STATUS"] == "failed"
    missing_docs_state_markers = parse_markers(
        "\n".join(
            [
                "<!-- MERGLBOT_REVIEW_VERDICT: approved_for_closeout -->",
                "<!-- MERGLBOT_REVIEW_STATUS: success -->",
            ]
        )
    )
    docs_state_marker_present = (
        "MERGLBOT_DOCUMENTATION_OBLIGATION_STATE" in missing_docs_state_markers
    )
    docs_state = missing_docs_state_markers.get(
        "MERGLBOT_DOCUMENTATION_OBLIGATION_STATE",
        "",
    )
    blockers = []
    if not docs_state_marker_present:
        docs_state = "unknown"
        blockers.append("missing_or_invalid_documentation_obligation_state")
    if docs_state_blocks_closeout(
        missing_docs_state_markers["MERGLBOT_REVIEW_VERDICT"],
        docs_state,
    ):
        blockers.append("review_docs_state_blocks_closeout")
    # v3-era rule only: a v3 receipt was expected to emit the docs-state marker.
    assert blockers == [
        "missing_or_invalid_documentation_obligation_state",
        "review_docs_state_blocks_closeout",
    ]
    # --- v6 surface -------------------------------------------------------
    # A real, approved v6 check-run receipt (marker set verified live against
    # merglbot-public/docs check-run summaries) must verify with zero blockers
    # even though it carries none of the v3/v4-era markers.
    v6_summary = "\n".join(
        [
            "## Merglbot PR Assistant local-primary",
            "<!-- MERGLBOT_PR_ASSISTANT_V6: true -->",
            "<!-- MERGLBOT_REVIEW_RECEIPT_SCHEMA_VERSION: 1 -->",
            "<!-- MERGLBOT_REVIEW_SOURCE: o/r#1 -->",
            "<!-- MERGLBOT_REVIEW_HEAD_SHA: abc123 -->",
            "<!-- MERGLBOT_REVIEW_RUN_ID: local-primary:lpwlease00 -->",
            "<!-- MERGLBOT_RUN_ID: pr-assistant-v6:local-primary:lpwlease00 -->",
            "<!-- MERGLBOT_RECEIPT_SURFACE: check_run_summary -->",
            "<!-- MERGLBOT_MARKER_STATUS: parseable_receipt -->",
            "<!-- MERGLBOT_REVIEW_STATUS: success -->",
            "<!-- MERGLBOT_REVIEW_VERDICT: approved_for_closeout -->",
            "<!-- MERGLBOT_PROVIDER_DEGRADED: false -->",
            "<!-- MERGLBOT_PROVIDER_DEGRADED_REASON: none -->",
            "<!-- MERGLBOT_REVIEW_MODE: standard -->",
            "<!-- MERGLBOT_ACTIONABLE_FINDINGS_COUNT: 0 -->",
            "<!-- MERGLBOT_AUTONOMOUS_NEXT_ACTION: safe_to_merge -->",
            "<!-- MERGLBOT_LOCAL_PRIMARY_ENGINE_EVIDENCE: codex:pass,claude:pass -->",
        ]
    )
    v6_markers = parse_markers(v6_summary)
    for never_emitted in V6_NEVER_EMITTED_ON_CHECK_RUN:
        assert never_emitted not in v6_markers
    assert evaluate_v6_receipt(v6_markers, "success", "abc123") == []
    # Coverage gate: the measured platform#1039 shape (no engine produced a
    # verdict) must block even though every other marker above is merge-clean.
    assert evaluate_v6_receipt(
        parse_markers(
            v6_summary.replace("codex:pass,claude:pass", "codex:skipped,claude:skipped")
        ),
        "success",
        "abc123",
    ) == ["no_engine_produced_verdict"]
    # docs#1383: a carried-forward receipt is current-head evidence; it blocks
    # only for a consumer that demands a verified engine run on this head.
    carried_summary = v6_summary.replace(
        "MERGLBOT_REVIEW_MODE: standard -->",
        "MERGLBOT_REVIEW_MODE: standard -->\n<!-- MERGLBOT_REVIEW_MODEL: carried-forward -->",
    )
    carried_markers = parse_markers(carried_summary)
    assert receipt_is_carried_forward(carried_markers)
    assert not receipt_is_carried_forward(v6_markers)
    # An engine row naming carried-forward is carried evidence even without the synthesis marker.
    assert receipt_is_carried_forward(
        parse_markers(v6_summary + "\n<!-- MERGLBOT_LOCAL_PRIMARY_ENGINE_MODELS: codex:gpt-6,claude:carried-forward -->")
    )
    assert not receipt_is_carried_forward(
        parse_markers(v6_summary + "\n<!-- MERGLBOT_LOCAL_PRIMARY_ENGINE_MODELS: codex:gpt-6,claude:claude-opus-5-5 -->")
    )
    assert evaluate_v6_receipt(carried_markers, "success", "abc123") == []
    assert evaluate_v6_receipt(
        carried_markers, "success", "abc123", require_verified_engine_run=True
    ) == ["carried_forward_not_verified_engine_run"]
    assert evaluate_v6_receipt(
        v6_markers, "success", "abc123", require_verified_engine_run=True
    ) == []
    # A carried receipt still binds to the live head; a stale head is denied.
    assert evaluate_v6_receipt(carried_markers, "success", "def456") == [
        "merglbot_review_head_sha_mismatch"
    ]
    assert carried_forward_source(
        [
            "## Review carried forward (unchanged diff)\n"
            "<!-- MERGLBOT_REVIEW_CARRIED_FROM: 5bf623e1f3fc0000000000000000000000000000 -->\n"
            "<!-- MERGLBOT_REVIEW_DIFF_SHA256: 885f38f15cc3e6b9885f38f15cc3e6b9885f38f15cc3e6b9885f38f15cc3e6b9 -->"
        ]
    ) == {
        "carried_from": "5bf623e1f3fc0000000000000000000000000000",
        "diff_sha256": "885f38f15cc3e6b9885f38f15cc3e6b9885f38f15cc3e6b9885f38f15cc3e6b9",
    }
    assert carried_forward_source(["no markers here"]) == {"carried_from": None, "diff_sha256": None}
    # The body naming the live head wins over a newer one that does not.
    assert carried_forward_source(
        [
            "<!-- MERGLBOT_REVIEW_HEAD_SHA: abc123 -->\n<!-- MERGLBOT_REVIEW_CARRIED_FROM: 1111111 -->",
            "<!-- MERGLBOT_REVIEW_CARRIED_FROM: 2222222 -->",
        ],
        "abc123",
    )["carried_from"] == "1111111"
    assert run_fixture_cases()
    # PR binding: check runs are commit-scoped, so an approval published for one
    # PR must never satisfy another PR sharing the same head commit.
    assert expected_review_source("o/r", 1) == "o/r#1"
    assert evaluate_v6_receipt(
        v6_markers, "success", "abc123", repo="o/r", pr_number=1
    ) == []
    assert evaluate_v6_receipt(
        v6_markers, "success", "abc123", repo="o/r", pr_number=2
    ) == ["review_source_pr_mismatch:o/r#1"]
    unbound = dict(v6_markers)
    unbound.pop("MERGLBOT_REVIEW_SOURCE")
    assert "missing_review_source_binding" in evaluate_v6_receipt(
        unbound, "success", "abc123", repo="o/r", pr_number=1
    )
    # The Checks API PR list corroborates but never replaces the marker: it is
    # empty for every merged PR, so emptiness must stay silent.
    assert evaluate_v6_receipt(
        v6_markers, "success", "abc123", repo="o/r", pr_number=1,
        linked_pull_requests=[],
    ) == []
    assert "check_run_not_linked_to_pr" in evaluate_v6_receipt(
        v6_markers, "success", "abc123", repo="o/r", pr_number=1,
        linked_pull_requests=[2, 3],
    )
    # Head binding, degradation and findings are still fail-closed.
    assert evaluate_v6_receipt(v6_markers, "success", "def456") == [
        "merglbot_review_head_sha_mismatch"
    ]
    assert "v6_check_run_not_success" in evaluate_v6_receipt(
        v6_markers, "action_required", "abc123"
    )
    degraded_markers = parse_markers(
        v6_summary.replace(
            "MERGLBOT_PROVIDER_DEGRADED: false", "MERGLBOT_PROVIDER_DEGRADED: true"
        )
    )
    assert "provider_degraded_or_missing" in evaluate_v6_receipt(
        degraded_markers, "success", "abc123"
    )
    absent_degraded_markers = dict(v6_markers)
    absent_degraded_markers.pop("MERGLBOT_PROVIDER_DEGRADED")
    assert "provider_degraded_or_missing" in evaluate_v6_receipt(
        absent_degraded_markers, "success", "abc123"
    )
    findings_markers = parse_markers(
        v6_summary.replace(
            "MERGLBOT_ACTIONABLE_FINDINGS_COUNT: 0",
            "MERGLBOT_ACTIONABLE_FINDINGS_COUNT: 7",
        )
    )
    assert "actionable_findings_require_fix" in evaluate_v6_receipt(
        findings_markers, "failure", "abc123"
    )
    assert findings_count_blocks("7")
    assert not findings_count_blocks("0")
    assert not findings_count_blocks("unknown")
    assert not findings_count_blocks("")
    # `unknown` is not a zero: the findings payload was never delivered.
    assert findings_count_is_delivered_zero("0")
    assert not findings_count_is_delivered_zero("unknown")
    assert not findings_count_is_delivered_zero("")
    unknown_count_markers = parse_markers(
        v6_summary.replace(
            "MERGLBOT_ACTIONABLE_FINDINGS_COUNT: 0",
            "MERGLBOT_ACTIONABLE_FINDINGS_COUNT: unknown",
        )
    )
    assert "actionable_findings_count_not_delivered" in evaluate_v6_receipt(
        unknown_count_markers, "success", "abc123"
    )
    # An absent docs-state marker is the v6 norm and must not block.
    assert "review_docs_state_blocks_closeout" not in evaluate_v6_receipt(
        v6_markers, "success", "abc123"
    )
    # A REPORTED missing/unknown docs state still blocks.
    reported_docs_state_markers = dict(v6_markers)
    reported_docs_state_markers["MERGLBOT_DOCUMENTATION_OBLIGATION_STATE"] = "missing"
    assert "review_docs_state_blocks_closeout" in evaluate_v6_receipt(
        reported_docs_state_markers, "success", "abc123"
    )
    # Run selection: newest COMPLETED run from the TRUSTED producer wins; any
    # pending trusted v6 run means WAIT.
    trusted_app = {
        "id": TRUSTED_V6_CHECK_APP_ID,
        "slug": TRUSTED_V6_CHECK_APP_SLUG,
        "owner": {"login": TRUSTED_V6_CHECK_APP_OWNER},
    }
    completed_old = {
        "name": V6_CHECK_NAME,
        "status": "completed",
        "completed_at": "2026-08-14T10:00:00Z",
        "id": 1,
        "app": trusted_app,
    }
    completed_new = {
        "name": V6_CHECK_NAME,
        "status": "completed",
        "completed_at": "2026-08-14T11:00:00Z",
        "id": 2,
        "app": trusted_app,
    }
    other_check = {"name": "ci", "status": "in_progress", "id": 3}
    selected, pending, untrusted = select_v6_check_run(
        [completed_old, completed_new, other_check]
    )
    assert selected is completed_new and pending is False and untrusted == []
    selected, pending, untrusted = select_v6_check_run(
        [
            completed_new,
            {
                "name": V6_CHECK_NAME,
                "status": "in_progress",
                "id": 4,
                "app": trusted_app,
            },
        ]
    )
    assert selected is completed_new and pending is True and untrusted == []
    assert select_v6_check_run([other_check]) == (None, False, [])
    # Trust root: the check-run NAME is not authority. A run with the right name
    # and a perfect marker block, produced by anyone else, is never selectable.
    forged = {
        "name": V6_CHECK_NAME,
        "status": "completed",
        "completed_at": "2026-08-14T12:00:00Z",
        "id": 5,
        "app": {"id": 15368, "slug": "github-actions", "owner": {"login": "github"}},
    }
    selected, pending, untrusted = select_v6_check_run([forged])
    assert selected is None and pending is False
    assert untrusted == ["app_id=15368"]
    # A forged run must not shadow the genuine one either.
    selected, _, untrusted = select_v6_check_run([completed_new, forged])
    assert selected is completed_new and untrusted == ["app_id=15368"]
    # Missing `app` fails closed rather than being ignored.
    assert v6_producer_rejection({"name": V6_CHECK_NAME}) == "missing_app"
    # A renamed app keeps its immutable id but must still fail closed loudly.
    renamed = {"app": {**trusted_app, "slug": "merglbot-pr-assistant-v7"}}
    assert v6_producer_rejection(renamed) == "app_slug='merglbot-pr-assistant-v7'"
    foreign_owner = {"app": {**trusted_app, "owner": {"login": "evil-org"}}}
    assert v6_producer_rejection(foreign_owner) == "app_owner='evil-org'"
    assert v6_producer_rejection({"app": trusted_app}) == ""
    # v3 surface: a non-numeric run id can never be provenance-verified, and a
    # successful lookup with no workflow path proves nothing either.
    assert v3_run_provenance("pr-assistant-v6:local-primary:forged", lambda _: {}) == (
        ["invalid_v3_run_id"],
        "",
    )
    assert v3_run_provenance("42", lambda _: {"path": ""}) == (
        ["review_run_path_unknown"],
        "",
    )
    assert v3_run_provenance(
        "42", lambda _: {"path": ".github/workflows/merglbot-pr-v3-on-demand.yml"}
    ) == ([], ".github/workflows/merglbot-pr-v3-on-demand.yml")
    # Run identity: the two run-id markers must name one producing run.
    assert run_lease_id("pr-assistant-v6:local-primary:lease9") == "lease9"
    assert run_lease_id("local-primary:lease9") == "lease9"
    assert run_lease_id("42") == "42"
    assert run_identity_blockers(v6_markers) == []
    assert run_identity_blockers(
        {"MERGLBOT_RUN_ID": "pr-assistant-v6:local-primary:a"}
    ) == ["missing_review_run_identity"]
    assert run_identity_blockers(
        {
            "MERGLBOT_RUN_ID": "pr-assistant-v6:local-primary:a",
            "MERGLBOT_REVIEW_RUN_ID": "local-primary:b",
        }
    ) == ["review_run_id_mismatch"]
    # Check-run pages from `gh api --paginate --slurp` flatten into one list.
    assert flatten_check_run_pages(
        [{"check_runs": [{"id": 1}]}, {"check_runs": [{"id": 2}]}]
    ) == [{"id": 1}, {"id": 2}]
    assert flatten_check_run_pages({"check_runs": [{"id": 1}]}) == [{"id": 1}]
    assert flatten_check_run_pages([]) == []
    blockers: list[str] = []
    status = "blocked"
    verdict = "changes_required"
    if status != "success" or verdict != "approved_for_closeout":
        blockers.append("review_not_approved_for_closeout")
    assert blockers == ["review_not_approved_for_closeout"]
    assert expected_run_url("https://github.enterprise.example/o/r/pull/42", "123") == (
        "https://github.enterprise.example/o/r/actions/runs/123"
    )
    assert (
        ".github/workflows/merglbot-pr-assistant-v3-on-demand.yml"
        in PR_ASSISTANT_WORKFLOW_PATHS
    )
    assert (
        ".github/workflows/merglbot-pr-v3-on-demand.yml" in PR_ASSISTANT_WORKFLOW_PATHS
    )
    print(json.dumps({"ok": True, "self_test": "passed"}))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", help="Repository in owner/name form")
    parser.add_argument("--pr", type=int, help="Pull request number")
    parser.add_argument(
        "--surface",
        choices=("auto", "v6", "v3"),
        default="auto",
        help=(
            "Receipt surface to verify. 'auto' (default) reads the active v6 "
            "check-run receipt and fails closed when none is present; 'v6' is "
            "the same without any fallback path; 'v3' reads the historical PR "
            "comment surface only."
        ),
    )
    parser.add_argument(
        "--allow-v3-fallback",
        action="store_true",
        help=(
            "With --surface auto, fall back to the historical v3 PR-comment "
            "surface when no v6 check run is present. Off by default: the v3 "
            "surface has a weaker trust root, so downgrading to it must be an "
            "explicit choice rather than the consequence of not seeing a check."
        ),
    )
    parser.add_argument(
        "--require-verified-engine-run",
        action="store_true",
        help=(
            "Deny a carried-forward receipt (MERGLBOT_REVIEW_MODEL: carried-forward). "
            "Default off: a carried receipt is current-head evidence under "
            "PR_POLICY.md section 3.4.1. Consumers that need a verified engine run "
            "on this exact head (dual-engine authority lane, manual-merge coverage) "
            "set it."
        ),
    )
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        return self_test()
    if not args.repo or not args.pr:
        parser.error("--repo and --pr are required unless --self-test is used")

    result = verify(
        args.repo,
        args.pr,
        surface=args.surface,
        allow_v3_fallback=args.allow_v3_fallback,
        require_verified_engine_run=args.require_verified_engine_run,
    )
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
