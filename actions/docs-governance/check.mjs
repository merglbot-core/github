// docs-governance — SSOT-aware documentation obligation classifier.
// Deterministic port of agents-orchestrator documentationObligation.ts semantics
// (impact paths, markdown evidence, test-only + dependabot exemptions), extended with
// two additional evidence routes: an SSOT sync marker and an explicit waiver.
// Contract: emits DOCS_GOVERNANCE_* markers to the step summary + stdout.
// advisory mode always exits 0; enforce mode exits 1 ONLY on state `missing`.
// `unknown` (no changed-file data) is fail-open with a warning in both modes.
import fs from "node:fs";
import path from "node:path";
import { execFileSync } from "node:child_process";

const MODE = (process.env.DG_MODE || "advisory").trim();
const ACTION_DIR = process.env.DG_ACTION_PATH || path.dirname(new URL(import.meta.url).pathname);

const DEFAULT_IMPACT_PREFIXES = [
  "apps/", "packages/", "src/", "services/", "scripts/", "tools/", "terraform/", ".github/workflows/",
];
const DEFAULT_IMPACT_FILES = ["package.json", "pyproject.toml", "Dockerfile"];
const TEST_MARKERS = ["/test/", "/tests/", "/__tests__/", ".test.", ".spec."];
const SSOT_SYNC_RE = /MERGLBOT_DOCS_SYNC:\s*merglbot-public\/docs#(\d+)/;
const WAIVER_LABEL = "docs-impact: none";
const WAIVER_REASON_RE = /DOCS_IMPACT_NONE_REASON:\s*(\S.*)/;

function loadSnapshot() {
  // Bundled snapshot of merglbot-public/docs ssot-map.yaml (synced by workflow).
  // Minimal YAML-subset parse intentionally avoided: the sync workflow converts to JSON.
  const p = path.join(ACTION_DIR, "..", "..", "config", "ssot-map.snapshot.json");
  try {
    return JSON.parse(fs.readFileSync(p, "utf8"));
  } catch {
    return null; // snapshot absent -> defaults-only behaviour
  }
}

function readEvent() {
  try {
    return JSON.parse(fs.readFileSync(process.env.DG_EVENT_PATH, "utf8"));
  } catch {
    return {};
  }
}

function changedFiles(event) {
  const base = event.pull_request?.base?.sha;
  const head = event.pull_request?.head?.sha;
  if (!base || !head) return null;
  const tryDiff = () => {
    const out = execFileSync("git", ["diff", "--name-only", `${base}...HEAD`], { encoding: "utf8" });
    return out.split("\n").map((l) => l.trim()).filter(Boolean);
  };
  try {
    return tryDiff(); // base already present (full clone / local)
  } catch {
    /* base missing — fetch it */
  }
  try {
    execFileSync("git", ["fetch", "--no-tags", "--depth=1", "origin", base], { stdio: "ignore" });
    return tryDiff();
  } catch {
    /* by-sha fetch refused — deepen from default remote head */
  }
  try {
    execFileSync("git", ["fetch", "--no-tags", "--depth=100", "origin"], { stdio: "ignore" });
    return tryDiff();
  } catch {
    return null; // unknown
  }
}

function isTestOnly(file) {
  const lower = `/${file.toLowerCase()}`;
  return TEST_MARKERS.some((m) => lower.includes(m));
}

function classify(files, cfg) {
  const impactPrefixes = cfg.impactPrefixes;
  const impactFiles = cfg.impactFiles;
  const impact = [];
  const evidence = [];
  for (const f of files) {
    const lower = f.toLowerCase();
    if (lower.endsWith(".md") || lower.endsWith(".mdx")) {
      evidence.push(f);
      continue;
    }
    if (isTestOnly(f)) continue;
    if (impactPrefixes.some((p) => f.startsWith(p)) || impactFiles.includes(f)) {
      impact.push(f);
    }
  }
  return { impact, evidence };
}

function emit(state, reason, ssotTargets, extra = []) {
  const lines = [
    "## docs-governance",
    "",
    `DOCS_GOVERNANCE_CONTRACT: 1`,
    `DOCS_GOVERNANCE_MODE: ${MODE}`,
    `DOCS_GOVERNANCE_STATE: ${state}`,
    `DOCS_GOVERNANCE_REASON: ${reason}`,
    `DOCS_GOVERNANCE_SSOT_TARGETS: ${ssotTargets.join(",") || "-"}`,
    ...extra,
  ];
  const text = lines.join("\n") + "\n";
  process.stdout.write(text);
  if (process.env.GITHUB_STEP_SUMMARY) fs.appendFileSync(process.env.GITHUB_STEP_SUMMARY, text);
}

function main() {
  const event = readEvent();
  const repo = process.env.DG_REPO || "";
  const snapshot = loadSnapshot();
  const repoCfg = snapshot?.repos?.[repo] || {};
  const defaults = snapshot?.defaults || {};
  const overridePrefixes = (process.env.DG_IMPACT_PREFIXES || "")
    .split(",").map((s) => s.trim()).filter(Boolean);
  const cfg = {
    impactPrefixes: overridePrefixes.length
      ? overridePrefixes
      : repoCfg.impact_prefixes ?? defaults.impact_prefixes ?? DEFAULT_IMPACT_PREFIXES,
    impactFiles: repoCfg.impact_files ?? defaults.impact_files ?? DEFAULT_IMPACT_FILES,
  };
  const ssotTargets = repoCfg.ssot_docs || [];

  // Dependabot manifest-only exemption (mirrors the ts classifier).
  const actor = event.pull_request?.user?.login || process.env.GITHUB_ACTOR || "";
  const body = event.pull_request?.body || "";
  const labels = (event.pull_request?.labels || []).map((l) => String(l.name || "").toLowerCase());

  const files = changedFiles(event);
  if (files === null) {
    emit("unknown", "changed_files_unavailable", ssotTargets, [
      "", "> Fail-open: deterministic file list unavailable (non-PR event or fetch failure).",
    ]);
    return; // fail-open in both modes
  }

  if (/^dependabot(\[bot\])?$/.test(actor)) {
    const manifestOnly = files.every((f) =>
      /(^|\/)(package(-lock)?\.json|yarn\.lock|pnpm-lock\.yaml|requirements[^/]*\.txt|poetry\.lock|Cargo\.(toml|lock)|go\.(mod|sum)|\.github\/dependabot\.yml)$/.test(f)
      || f.startsWith(".github/workflows/"));
    if (manifestOnly) {
      emit("not_required", "dependabot_manifest_only", ssotTargets);
      return;
    }
  }

  const { impact, evidence } = classify(files, cfg);
  if (impact.length === 0) {
    emit("not_required", "no_impact_paths", ssotTargets);
    return;
  }
  if (evidence.length > 0) {
    emit("satisfied", "local_markdown_evidence", ssotTargets, [
      "", `Evidence: ${evidence.slice(0, 5).join(", ")}`,
    ]);
    return;
  }
  const sync = body.match(SSOT_SYNC_RE);
  if (sync) {
    emit("satisfied_via_ssot_sync", `ssot_pr_reference_docs_${sync[1]}`, ssotTargets, [
      "", `> SSOT sync: merglbot-public/docs#${sync[1]} (existence cross-validated asynchronously by docs-keeper).`,
    ]);
    return;
  }
  if (labels.includes(WAIVER_LABEL)) {
    const reason = body.match(WAIVER_REASON_RE);
    if (reason) {
      emit("satisfied_via_waiver", "docs_impact_none_with_reason", ssotTargets, [
        "", `> Waiver reason: ${reason[1].slice(0, 200)}`,
      ]);
      return;
    }
    emit("missing", "waiver_label_without_reason", ssotTargets, [
      "", "> Label `docs-impact: none` requires a `DOCS_IMPACT_NONE_REASON:` line in the PR body.",
    ]);
  } else {
    emit("missing", "impact_without_docs_evidence", ssotTargets, [
      "",
      `Impact paths (${impact.length}): ${impact.slice(0, 5).join(", ")}`,
      ssotTargets.length
        ? `Owning SSOT docs: ${ssotTargets.join(", ")} — update them (or this repo's docs) and reference via \`MERGLBOT_DOCS_SYNC: merglbot-public/docs#<pr>\`.`
        : "Add same-PR markdown evidence, an SSOT sync marker, or the `docs-impact: none` label + reason.",
    ]);
  }
  if (MODE === "enforce") process.exitCode = 1;
}

main();
