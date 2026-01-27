#!/usr/bin/env python3
"""
PR Assistant v3 — Codex cost/value audit helper.

Collects recent PR Assistant v3 review comments across target repos and computes
lightweight cost/value proxies (latency, output size, verdicts, reactions, etc.).

Notes:
- Read-only (GitHub API only via `gh api`)
- Never prints tokens/secrets; stores only numeric telemetry
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


MARKER = "<!-- MERGLBOT_PR_ASSISTANT_V3 -->"
BOT_LOGINS = {"github-actions", "github-actions[bot]"}


class GhApiError(RuntimeError):
    pass


def parse_iso8601(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def gh_api(
    endpoint: str,
    *,
    method: str = "GET",
    fields: dict[str, str] | None = None,
    paginate: bool = False,
    slurp: bool = False,
    timeout_s: int = 180,
    raw: bool = False,
) -> str | bytes:
    cmd = ["gh", "api"]

    # gh api can return 404 for list endpoints with query fields unless method is explicit.
    if method != "GET" or fields is not None:
        cmd += ["-X", method]
    cmd.append(endpoint)

    if paginate:
        cmd.append("--paginate")
    if slurp:
        cmd.append("--slurp")
    if fields:
        for k, v in fields.items():
            cmd += ["-f", f"{k}={v}"]

    p = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=not raw,
        timeout=timeout_s,
    )
    if p.returncode != 0:
        stderr = (p.stderr or "").strip()
        raise GhApiError(f"gh api failed ({endpoint}): {stderr[:400]}")

    return p.stdout if raw else (p.stdout or "").strip()


def gh_api_json(*args: Any, **kwargs: Any) -> Any:
    out = gh_api(*args, **kwargs)
    if isinstance(out, (bytes, bytearray)):
        raise GhApiError("Expected JSON text, got bytes")
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError as e:
        raise GhApiError(f"Failed to decode JSON: {e}")


def flatten_pages(pages: Any) -> list[Any]:
    if pages is None:
        return []
    if isinstance(pages, list):
        if pages and all(isinstance(p, list) for p in pages):
            out: list[Any] = []
            for p in pages:
                out.extend(p)
            return out
        return pages
    return [pages]


def read_target_repos(path: Path) -> list[str]:
    repos: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = re.sub(r"#.*$", "", raw).strip()
        if not line:
            continue
        repos.append(line)
    return repos


def extract_hidden(body: str, key: str) -> str | None:
    m = re.search(rf"<!--\s*{re.escape(key)}\s*:\s*(.*?)\s*-->", body)
    return m.group(1).strip() if m else None


def extract_review_mode(body: str) -> str | None:
    m = re.search(r"^\|\s*Review Mode\s*\|\s*`(full|light)`\s*\|", body, flags=re.IGNORECASE | re.MULTILINE)
    mode = m.group(1) if m else None
    return mode.lower() if mode else None


def extract_table_words(body: str, label: str) -> int | None:
    m = re.search(
        rf"^\|\s*{re.escape(label)}\s*\|\s*([0-9]+)\s+words\s*\|",
        body,
        flags=re.IGNORECASE | re.MULTILINE,
    )
    if not m:
        return None
    try:
        return int(m.group(1))
    except Exception:
        return None


def normalize_verdict(raw: str) -> str:
    v = raw.strip()
    v = re.sub(r"[`*]+", "", v)
    v = re.sub(r"_+", " ", v)
    v = re.sub(r"\s+", " ", v).strip()
    if re.match(r"(?i)^changes\s+needed\b", v):
        return "CHANGES_NEEDED"
    if re.match(r"(?i)^approve\b", v):
        return "APPROVE"
    return "UNKNOWN"


def extract_verdict_from_body(body: str) -> str:
    m = re.search(r"(?im)^Verdict:\s*(.+?)\s*$", body)
    return normalize_verdict(m.group(1)) if m else "UNKNOWN"


def count_commits_after(commits: list[dict[str, Any]], after: dt.datetime) -> int:
    n = 0
    for c in commits:
        commit = c.get("commit") or {}
        committer = commit.get("committer") or {}
        author = commit.get("author") or {}
        date_str = committer.get("date") or author.get("date")
        if not date_str:
            continue
        try:
            ts = parse_iso8601(date_str)
        except Exception:
            continue
        if ts > after:
            n += 1
    return n


def avg(nums: list[float]) -> float | None:
    return (sum(nums) / len(nums)) if nums else None


@dataclass
class CandidateComment:
    repo: str
    comment_id: int
    issue_url: str
    html_url: str
    created_at: dt.datetime
    body: str


def list_repo_issue_comments_since(repo: str, since_iso: str) -> list[dict[str, Any]]:
    pages = gh_api_json(
        f"repos/{repo}/issues/comments",
        fields={"since": since_iso, "per_page": "100"},
        paginate=True,
        slurp=True,
        timeout_s=240,
    )
    return flatten_pages(pages)


def fetch_reactions(repo: str, comment_id: int) -> dict[str, int]:
    pages = gh_api_json(
        f"repos/{repo}/issues/comments/{comment_id}/reactions",
        fields={"per_page": "100"},
        paginate=True,
        slurp=True,
        timeout_s=120,
    )
    reactions = flatten_pages(pages)
    up = sum(1 for r in reactions if r.get("content") == "+1")
    down = sum(1 for r in reactions if r.get("content") == "-1")
    return {"up": up, "down": down}


def fetch_pr(repo: str, pr_number: int) -> dict[str, Any]:
    pr = gh_api_json(f"repos/{repo}/pulls/{pr_number}")
    return pr if isinstance(pr, dict) else {}


def fetch_pr_commits(repo: str, pr_number: int) -> list[dict[str, Any]]:
    pages = gh_api_json(
        f"repos/{repo}/pulls/{pr_number}/commits",
        fields={"per_page": "100"},
        paginate=True,
        slurp=True,
        timeout_s=240,
    )
    return flatten_pages(pages)


def fetch_run(repo: str, run_id: int) -> dict[str, Any]:
    run = gh_api_json(f"repos/{repo}/actions/runs/{run_id}")
    return run if isinstance(run, dict) else {}


def pick_metrics_artifact(artifacts: list[dict[str, Any]], pr_number: int, run_id: int) -> dict[str, Any] | None:
    expected = f"review-metrics-{pr_number}-{run_id}"
    for a in artifacts:
        if a.get("name") == expected:
            return a
    for a in artifacts:
        name = a.get("name") or ""
        if name.startswith("review-metrics-") and name.endswith(str(run_id)) and f"-{pr_number}-" in name:
            return a
    for a in artifacts:
        name = a.get("name") or ""
        if name.startswith("review-metrics-"):
            return a
    return None


def download_review_metrics(repo: str, pr_number: int, run_id: int, tmp_dir: Path) -> dict[str, Any] | None:
    artifacts_resp = gh_api_json(f"repos/{repo}/actions/runs/{run_id}/artifacts")
    if not isinstance(artifacts_resp, dict):
        return None
    artifacts = artifacts_resp.get("artifacts") or []
    if not isinstance(artifacts, list) or not artifacts:
        return None

    artifact = pick_metrics_artifact(artifacts, pr_number, run_id)
    if not artifact:
        return None
    artifact_id = artifact.get("id")
    if not isinstance(artifact_id, int):
        return None

    zip_bytes = gh_api(f"repos/{repo}/actions/artifacts/{artifact_id}/zip", raw=True, timeout_s=240)
    if not isinstance(zip_bytes, (bytes, bytearray)):
        return None

    zip_path = tmp_dir / f"review-metrics-{repo.replace('/', '_')}-{run_id}.zip"
    zip_path.write_bytes(zip_bytes)

    extract_dir = tmp_dir / f"review-metrics-{repo.replace('/', '_')}-{run_id}"
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as z:
        z.extractall(extract_dir)

    metrics_files = list(extract_dir.rglob("review-metrics.json"))
    if not metrics_files:
        return None

    try:
        metrics = json.loads(metrics_files[0].read_text(encoding="utf-8"))
    except Exception:
        return None
    return metrics if isinstance(metrics, dict) else None


def safe_int(v: Any) -> int:
    try:
        return int(v)
    except Exception:
        return 0


def safe_float(v: Any) -> float | None:
    try:
        return float(v)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-days", type=int, default=120)
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--review-mode", choices=["any", "full", "light"], default="any")
    ap.add_argument("--target-repos", default="scripts/pr-assistant/target-repos.txt")
    ap.add_argument("--out-json", default="")
    ap.add_argument("--out-md", default="")
    args = ap.parse_args()

    repo_root = Path(__file__).resolve().parents[2]
    target_repos_path = (repo_root / args.target_repos).resolve()
    if not target_repos_path.exists():
        print(f"Target repos file not found: {target_repos_path}", file=sys.stderr)
        return 2

    repos = read_target_repos(target_repos_path)
    if not repos:
        print("No repos in target list", file=sys.stderr)
        return 2

    since_dt = dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=args.since_days)
    since_iso = since_dt.replace(hour=0, minute=0, second=0, microsecond=0).isoformat().replace("+00:00", "Z")

    print(
        f"Collecting PR Assistant v3 comments since {since_iso} across {len(repos)} repos (mode={args.review_mode})...",
        file=sys.stderr,
    )

    candidates: list[CandidateComment] = []
    for i, repo in enumerate(repos, start=1):
        try:
            comments = list_repo_issue_comments_since(repo, since_iso)
        except GhApiError as e:
            print(f"WARN: {repo}: {e}", file=sys.stderr)
            continue

        for c in comments:
            body = c.get("body") or ""
            if MARKER not in body:
                continue
            user = c.get("user") or {}
            login = (user.get("login") or "").strip()
            if login and login not in BOT_LOGINS:
                continue

            created_at_raw = c.get("created_at")
            if not created_at_raw:
                continue
            try:
                created_at = parse_iso8601(created_at_raw)
            except Exception:
                continue

            try:
                candidates.append(
                    CandidateComment(
                        repo=repo,
                        comment_id=int(c.get("id")),
                        issue_url=str(c.get("issue_url")),
                        html_url=str(c.get("html_url")),
                        created_at=created_at,
                        body=body,
                    )
                )
            except Exception:
                continue

        if i % 5 == 0:
            time.sleep(0.2)

    if not candidates:
        print("No PR Assistant v3 comments found", file=sys.stderr)
        return 1

    candidates.sort(key=lambda x: x.created_at, reverse=True)

    selected: list[CandidateComment] = []
    for c in candidates:
        if args.review_mode != "any":
            if extract_review_mode(c.body) != args.review_mode:
                continue
        selected.append(c)
        if len(selected) >= args.limit:
            break

    if not selected:
        print("No matching comments after applying filters", file=sys.stderr)
        return 1

    print(f"Selected {len(selected)} comments (from {len(candidates)} matches)", file=sys.stderr)

    out_rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        for idx, c in enumerate(selected, start=1):
            pr_num_str = c.issue_url.rstrip("/").split("/")[-1]
            try:
                pr_number = int(pr_num_str)
            except ValueError:
                print(f"WARN: {c.repo}: could not parse PR number from {c.issue_url}", file=sys.stderr)
                continue

            run_id_str = extract_hidden(c.body, "MERGLBOT_RUN_ID")
            run_id = int(run_id_str) if run_id_str and run_id_str.isdigit() else None

            verdict = extract_verdict_from_body(c.body)
            review_mode = extract_review_mode(c.body) or "unknown"
            diff_scope = extract_hidden(c.body, "MERGLBOT_DIFF_SCOPE")
            diff_range = extract_hidden(c.body, "MERGLBOT_DIFF_RANGE")

            reactions = {"up": 0, "down": 0}
            try:
                reactions = fetch_reactions(c.repo, c.comment_id)
            except GhApiError as e:
                print(f"WARN: reactions {c.repo}#{c.comment_id}: {e}", file=sys.stderr)

            pr = {}
            try:
                pr = fetch_pr(c.repo, pr_number)
            except GhApiError as e:
                print(f"WARN: PR fetch {c.repo}#{pr_number}: {e}", file=sys.stderr)

            merged_at = None
            merged_at_raw = pr.get("merged_at") if isinstance(pr, dict) else None
            if merged_at_raw:
                try:
                    merged_at = parse_iso8601(str(merged_at_raw))
                except Exception:
                    merged_at = None

            commits_after_review = None
            try:
                commits = fetch_pr_commits(c.repo, pr_number)
                commits_after_review = count_commits_after(commits, c.created_at)
            except GhApiError as e:
                print(f"WARN: commits {c.repo}#{pr_number}: {e}", file=sys.stderr)

            merge_latency_hours = None
            if merged_at is not None:
                merge_latency_hours = (merged_at - c.created_at).total_seconds() / 3600.0

            run_html_url = None
            run_conclusion = None
            run_duration_seconds = None
            metrics = None
            if run_id is not None:
                try:
                    run = fetch_run(c.repo, run_id)
                    run_html_url = run.get("html_url")
                    run_conclusion = run.get("conclusion")
                    started_at_raw = run.get("run_started_at") or run.get("created_at")
                    completed_at_raw = run.get("completed_at") or run.get("updated_at")
                    if started_at_raw and completed_at_raw:
                        started = parse_iso8601(str(started_at_raw))
                        completed = parse_iso8601(str(completed_at_raw))
                        run_duration_seconds = int((completed - started).total_seconds())
                except Exception:
                    run_duration_seconds = None

                try:
                    metrics = download_review_metrics(c.repo, pr_number, run_id, tmp_dir)
                except GhApiError as e:
                    print(f"WARN: artifact {c.repo}#{run_id}: {e}", file=sys.stderr)

            models = metrics.get("models") if isinstance(metrics, dict) else None
            findings = metrics.get("findings") if isinstance(metrics, dict) else None

            # Prefer artifact word counts, fall back to comment parsing.
            anthropic_words = None
            openai_words = None
            codex_words = None
            final_words = None
            if isinstance(metrics, dict):
                output = metrics.get("output") or {}
                if isinstance(output, dict):
                    anthropic_words = output.get("anthropic_words")
                    openai_words = output.get("openai_words")
                    codex_words = output.get("codex_words")
                    final_words = output.get("final_words")

            if anthropic_words is None:
                anthropic_words = extract_table_words(c.body, "Anthropic Output")
            if openai_words is None:
                openai_words = extract_table_words(c.body, "OpenAI Output")
            if codex_words is None:
                codex_words = extract_table_words(c.body, "Codex Output")
            if final_words is None:
                final_words = extract_table_words(c.body, "Final Review")

            codex_words_i = safe_int(codex_words)
            codex_ran = codex_words_i > 0
            merged_despite_changes_needed = bool(verdict == "CHANGES_NEEDED" and merged_at is not None)

            proxies = {
                "diff_blocks": c.body.count("```diff"),
                "checkboxes": len(re.findall(r"(?m)^[ \t]*[-*][ \t]*\[[ xX]\]", c.body)),
                "sec_rule_mentions": len(re.findall(r"MERGLBOT-SEC-[0-9]{3}", c.body)),
            }

            out_rows.append(
                {
                    "repository": c.repo,
                    "pr_number": pr_number,
                    "comment": {
                        "id": c.comment_id,
                        "url": c.html_url,
                        "created_at": c.created_at.isoformat().replace("+00:00", "Z"),
                        "run_id": run_id,
                    },
                    "review": {
                        "review_mode": review_mode,
                        "diff_scope": diff_scope,
                        "diff_range": diff_range,
                        "verdict": verdict,
                        "models": models,
                        "output_words": {
                            "anthropic": safe_int(anthropic_words),
                            "openai": safe_int(openai_words),
                            "codex": codex_words_i,
                            "final": safe_int(final_words),
                        },
                        "findings": findings,
                        "codex_ran": codex_ran,
                        "proxies": proxies,
                    },
                    "reactions": reactions,
                    "delivery": {
                        "commits_after_review": commits_after_review,
                        "merged_at": merged_at.isoformat().replace("+00:00", "Z") if merged_at else None,
                        "merge_latency_hours": merge_latency_hours,
                        "merged_despite_changes_needed": merged_despite_changes_needed,
                    },
                    "run": {
                        "html_url": run_html_url,
                        "conclusion": run_conclusion,
                        "duration_seconds": run_duration_seconds,
                    },
                }
            )

            print(
                f"[{idx}/{len(selected)}] {c.repo} PR #{pr_number} mode={review_mode} verdict={verdict} codex_words={codex_words_i} (+{reactions['up']}/-{reactions['down']})",
                file=sys.stderr,
            )

    if args.out_json:
        Path(args.out_json).write_text(json.dumps(out_rows, indent=2, sort_keys=True), encoding="utf-8")

    # Aggregate for markdown output
    total = len(out_rows)
    up = sum(r.get("reactions", {}).get("up", 0) for r in out_rows)
    down = sum(r.get("reactions", {}).get("down", 0) for r in out_rows)
    feedback = up + down
    satisfaction = (up * 100.0 / feedback) if feedback else None

    verdict_counts: dict[str, int] = {}
    codex_ran_count = 0
    codex_words_nonzero: list[int] = []
    durations: list[int] = []
    commits_after: list[int] = []
    merge_latency: list[float] = []
    merged_despite = 0

    for r in out_rows:
        verdict = (r.get("review", {}).get("verdict") or "UNKNOWN").upper()
        verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1

        codex_words_i = safe_int(r.get("review", {}).get("output_words", {}).get("codex"))
        if codex_words_i > 0:
            codex_ran_count += 1
            codex_words_nonzero.append(codex_words_i)

        dur = r.get("run", {}).get("duration_seconds")
        if isinstance(dur, int) and dur > 0:
            durations.append(dur)

        ca = r.get("delivery", {}).get("commits_after_review")
        if isinstance(ca, int):
            commits_after.append(ca)

        lat = safe_float(r.get("delivery", {}).get("merge_latency_hours"))
        if isinstance(lat, float):
            merge_latency.append(lat)

        if r.get("delivery", {}).get("merged_despite_changes_needed") is True:
            merged_despite += 1

    md_lines: list[str] = []
    md_lines.append("# PR Assistant v3 — Codex cost/value audit")
    md_lines.append("")
    md_lines.append(f"Generated: {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    md_lines.append(f"Window: since {since_iso}")
    md_lines.append(f"Filter: review_mode={args.review_mode}")
    md_lines.append("")
    md_lines.append("## Summary")
    md_lines.append("")
    md_lines.append("| Metric | Value |")
    md_lines.append("|--------|-------|")
    md_lines.append(f"| Reviews (n) | {total} |")
    md_lines.append(f"| Verdicts | {', '.join(f'{k}={v}' for k, v in sorted(verdict_counts.items()))} |")
    md_lines.append(f"| Codex ran (codex_words>0) | {codex_ran_count}/{total} |")
    md_lines.append(f"| 👍 Helpful | {up} |")
    md_lines.append(f"| 👎 Not helpful | {down} |")
    md_lines.append(f"| Satisfaction | {f'{satisfaction:.1f}%' if satisfaction is not None else 'N/A'} |")
    md_lines.append(f"| Avg Codex words (non-zero only) | {f'{avg([float(x) for x in codex_words_nonzero]):.0f}' if codex_words_nonzero else 'N/A'} |")
    md_lines.append(f"| Avg workflow duration (min) | {f'{avg([d / 60 for d in durations]):.1f}' if durations else 'N/A'} |")
    md_lines.append(f"| Avg commits-after-review | {f'{avg([float(x) for x in commits_after]):.2f}' if commits_after else 'N/A'} |")
    md_lines.append(f"| Avg merge latency (hours, merged only) | {f'{avg(merge_latency):.1f}' if merge_latency else 'N/A'} |")
    md_lines.append(f"| Merged despite CHANGES_NEEDED | {merged_despite} |")

    md_lines.append("")
    md_lines.append("## Runs")
    md_lines.append("")
    md_lines.append("| # | Repo | PR | Mode | Verdict | Codex words | Diff blocks | Checkboxes | MERGLBOT-SEC-* | 👍 | 👎 | Commits after | Merge latency (h) | Run | Comment |")
    md_lines.append("|---|------|----|------|--------|------------|------------|------------|---------------|----|----|--------------|------------------|-----|---------|")

    for i, r in enumerate(out_rows, start=1):
        repo = r.get("repository")
        prn = r.get("pr_number")
        mode = r.get("review", {}).get("review_mode")
        verdict = r.get("review", {}).get("verdict") or ""
        codexw = safe_int(r.get("review", {}).get("output_words", {}).get("codex"))
        proxies = r.get("review", {}).get("proxies") or {}
        diff_blocks = safe_int(proxies.get("diff_blocks"))
        checkboxes = safe_int(proxies.get("checkboxes"))
        sec_mentions = safe_int(proxies.get("sec_rule_mentions"))
        up_i = r.get("reactions", {}).get("up", 0)
        down_i = r.get("reactions", {}).get("down", 0)
        ca = r.get("delivery", {}).get("commits_after_review")
        lat = safe_float(r.get("delivery", {}).get("merge_latency_hours"))
        run_url = r.get("run", {}).get("html_url") or ""
        comment_url = r.get("comment", {}).get("url") or ""

        md_lines.append(
            "| "
            + " | ".join(
                [
                    str(i),
                    str(repo),
                    f"#{prn}",
                    str(mode),
                    str(verdict),
                    str(codexw),
                    str(diff_blocks),
                    str(checkboxes),
                    str(sec_mentions),
                    str(up_i),
                    str(down_i),
                    str(ca) if ca is not None else "",
                    f"{lat:.1f}" if isinstance(lat, float) else "",
                    f"[run]({run_url})" if run_url else "",
                    f"[comment]({comment_url})" if comment_url else "",
                ]
            )
            + " |"
        )

    md = "\n".join(md_lines) + "\n"
    if args.out_md:
        Path(args.out_md).write_text(md, encoding="utf-8")
    else:
        print(md)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
