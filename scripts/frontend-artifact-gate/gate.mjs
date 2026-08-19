#!/usr/bin/env node
// Frontend build-artifact gate — S5 preventive components 3, 4 and 5a
// (merglbot-core/infra#1912, prescribed by the 2026-08-05 external security audit).
//
// One gate over one input, deliberately. The audit lists a "build-manifest diff gate" and a
// "source-map / binary-artifact ban" as two components; they share an input (`dist/`), a trigger and
// a failure mode, and two baselines over one artifact means the one nobody re-runs goes stale.
//
// WHAT IT PREVENTS, and why the shape is what it is:
//
//   * `**/*.map` — source maps ship original sources. No SPA in this estate emits them today
//     (Vite's default is `sourcemap: false` and no vite.config.js overrides it), so this is pure
//     regression cover: it goes red the day someone sets `build.sourcemap: true`.
//
//   * A `sourceMappingURL` directive in the BYTES of a served text file — the other half of that
//     ban. `sourcemap: 'inline'` writes no `.map` at all, so the filename rule above sees a clean
//     build while the full source rides inside the bundle. Filenames catch `'hidden'`, the byte
//     scan catches `'inline'`; neither is sufficient alone.
//
//   * Extension allowlist, NOT a denylist. "A new public artifact type appears" is the audit's own
//     wording, and a denylist is open-by-default — it can only ever ban the types someone thought
//     of. Filenames are content-hashed here, so a type-based rule never churns on a rebuild.
//
//   * Image inventory by CONTENT HASH (5a). This is the only new control that would have caught
//     merglbot-public/website#470, the one confirmed client-data leak in this estate: twelve PNGs
//     holding a named client's ad-performance data, committed to the repo, bundled into
//     `dist/assets`, and republished through the npm package to every tenant web repo. Path-based
//     comparison would not have caught it — the paths were ordinary. The bytes were the problem.
//
// EXIT CODES — the distinction is load-bearing:
//   0  contract holds
//   1  violation: the artifact is wrong
//   2  CANNOT CONCLUDE: no dist/, no index.html, fewer files than the floor, or a malformed
//      baseline. A gate that returns 0 over an empty directory returns 0 over every build that
//      failed to produce anything, which is the failure mode this whole component exists to stop.
//
// Zero dependencies on purpose: this runs in a caller repo's CI right after `npm run build`, and a
// gate that needs its own install step is a gate that gets skipped when the install breaks.

import { createHash } from 'node:crypto'
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs'
import { join, relative, extname, sep } from 'node:path'

const EXIT_OK = 0
const EXIT_VIOLATION = 1
const EXIT_CANNOT_CONCLUDE = 2

// Image types, used twice: the 5a content-hash freeze, and (minus .svg) the not-text exclusion for
// the source-map byte scan. One list so the two cannot drift apart.
const IMAGE_EXTENSIONS = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.ico', '.avif'])

function fail(message) {
  console.error(`✖ ${message}`)
}

function cannotConclude(message) {
  console.error(`⚠ CANNOT CONCLUDE: ${message}`)
  process.exit(EXIT_CANNOT_CONCLUDE)
}

function walk(dir, base = dir, out = []) {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    const st = statSync(full)
    if (st.isDirectory()) walk(full, base, out)
    else out.push(relative(base, full).split(sep).join('/'))
  }
  return out
}

// A placeholder that `**/` becomes before `*` is expanded, so the second pass cannot chew the
// first's output. NUL because it is the one character a path, and so a glob over paths, cannot
// contain. A space would read tidier and would be wrong: filenames may contain spaces.
//
// 🔴 WRITTEN AS AN ESCAPE, NEVER AS A RAW BYTE. The first version of this file carried two literal
// NUL bytes here instead. That makes the file BINARY to git — `git diff` reported
// `Bin 0 -> 8332 bytes` and `--numstat` gave `-  -` — so all 202 lines were invisible in every diff
// the review gate fetched, and no engine ever reviewed a single line of the gate.
//
// It did not look like that from the outside. The symptom was a `blocked_missing_authority` verdict
// carrying `ACTIONABLE_FINDINGS_COUNT: unknown`, which reads like a permissions problem, and the PR
// sat waiting for an authority grant that would not have helped: granting it would have merged 202
// unreviewed lines.
const SEGMENT_SENTINEL = '\u0000'

// Minimal glob: supports `**/` prefix and a single `*` wildcard inside a segment. Deliberately not a
// full glob implementation — the baseline only needs suffix and directory patterns, and a
// hand-rolled regex that silently mis-parses is worse than a small documented subset.
function globToRegExp(pattern) {
  const escaped = pattern
    .replace(/[.+^${}()|[\]\\]/g, '\\$&')
    .replace(/\*\*\//g, SEGMENT_SENTINEL)
    .replace(/\*/g, '[^/]*')
    .replaceAll(SEGMENT_SENTINEL, '(?:.*/)?')
  return new RegExp(`^${escaped}$`)
}

function sha256(path) {
  return createHash('sha256').update(readFileSync(path)).digest('hex')
}

function loadBaseline(path) {
  if (!existsSync(path)) cannotConclude(`baseline not found: ${path}`)
  let baseline
  try {
    baseline = JSON.parse(readFileSync(path, 'utf8'))
  } catch (err) {
    cannotConclude(`baseline is not valid JSON: ${err.message}`)
  }

  // Validate BEFORE touching the artifact. A malformed baseline must never be able to reach a
  // per-file verdict — it would produce a pass or a fail that means nothing either way.
  for (const key of ['required_paths', 'banned_globs', 'allowed_extensions']) {
    if (!Array.isArray(baseline[key])) cannotConclude(`baseline.${key} must be an array`)
  }
  for (const entry of [...baseline.required_paths, ...baseline.banned_globs]) {
    if (!entry.reason) {
      cannotConclude(`every required_paths / banned_globs entry needs a "reason": ${JSON.stringify(entry)}`)
    }
    const dated = Boolean(entry.expires)
    const permanent = Boolean(entry.permanent)
    if (dated === permanent) {
      cannotConclude(
        `entry must declare exactly one of "expires" or "permanent": ${JSON.stringify(entry)}`,
      )
    }
  }
  if (typeof baseline.min_files !== 'number' || baseline.min_files < 1) {
    cannotConclude('baseline.min_files must be a positive number — it is the empty-build floor')
  }
  const inv = baseline.image_inventory
  if (inv && inv.mode === 'frozen' && !Array.isArray(inv.entries)) {
    cannotConclude('image_inventory.mode is "frozen" but entries is not an array')
  }
  return baseline
}

function main() {
  const args = process.argv.slice(2)
  const distIdx = args.indexOf('--dist')
  const baseIdx = args.indexOf('--baseline')
  if (distIdx === -1 || baseIdx === -1) {
    console.error('usage: gate.mjs --dist <dir> --baseline <file.json> [--extra-assets <dir>]')
    process.exit(EXIT_CANNOT_CONCLUDE)
  }
  const dist = args[distIdx + 1]
  const baseline = loadBaseline(args[baseIdx + 1])
  const extraIdx = args.indexOf('--extra-assets')
  const extraAssets = extraIdx === -1 ? null : args[extraIdx + 1]

  if (!existsSync(dist) || !statSync(dist).isDirectory()) {
    cannotConclude(`dist directory not found: ${dist}`)
  }
  const files = walk(dist)
  if (files.length === 0) cannotConclude(`${dist} is empty`)
  if (!files.includes('index.html')) {
    cannotConclude(`${dist}/index.html missing — this does not look like a built SPA`)
  }
  if (files.length < baseline.min_files) {
    cannotConclude(
      `${dist} holds ${files.length} files, below the floor of ${baseline.min_files}. ` +
        'A partial build must not be waved through as a clean one.',
    )
  }

  const violations = []

  for (const { glob, reason } of baseline.banned_globs) {
    const re = globToRegExp(glob)
    for (const f of files.filter((f) => re.test(f))) {
      violations.push(`banned artifact ${f} (matches ${glob}) — ${reason}`)
    }
  }

  for (const { path, reason } of baseline.required_paths) {
    if (!files.includes(path)) violations.push(`required file missing: ${path} — ${reason}`)
  }

  const allowed = new Set(baseline.allowed_extensions)
  for (const f of files) {
    const ext = extname(f).toLowerCase()
    if (!allowed.has(ext)) {
      violations.push(
        `${f} has undeclared extension "${ext || '(none)'}" — a new public artifact TYPE must be ` +
          'added to allowed_extensions deliberately, with review',
      )
    }
  }

  // 🔴 A source map does not have to be a FILE, so the `**/*.map` glob above is only half the ban.
  //
  // `build.sourcemap: 'inline'` appends the whole map, base64-encoded, into the bundle itself as a
  // `sourceMappingURL=data:` comment. No `.map` lands on disk, so dist/ looks exactly like a clean
  // build while the original sources ship inside a file that is already public. `'hidden'` is the
  // mirror case: the file exists and the comment does not.
  //
  // That makes this the check the header's own promise depends on — "it goes red the day someone
  // sets build.sourcemap: true" was true for `true` and false for `'inline'`, which is the setting
  // a person reaches for when they want maps without extra requests.
  //
  // Found by merglbot-core/merglbot-admin#744 review, which closed the same hole in admin's
  // separate build-side gate; this is the shared half.
  //
  // The matcher is anchored to a comment opener AT THE START OF A LINE. Both halves are needed and
  // each was learned the hard way:
  //
  //   * the bare word matches `const sourceMappingURL = …`;
  //   * a comment opener alone still matches `const banner = "docs: //# sourceMappingURL=x"`,
  //     because `//` can sit inside a string literal. Bundler runtime code and source-map tooling
  //     quote this syntax routinely.
  //
  // A false RED here is not a harmless over-catch: it blocks a release, and the fix everyone
  // reaches for is to stop trusting the check.
  //
  // Line-start is the discriminator that costs nothing, because a REAL inline directive is always
  // emitted on its own line at the end of the file — that is how every bundler writes it.
  //
  // Known residual, and deliberate: a template literal holding the directive at line start still
  // trips this, and a hand-placed mid-line directive would be missed. Neither is a shape a bundler
  // emits, and the first fails safe while the second is not reachable by the setting this guards.
  const SOURCE_MAPPING_DIRECTIVE = /^[ \t]*(?:\/\/|\/\*)\s*[#@]\s*sourceMappingURL\s*=/m
  //
  // 🔴 The scanned set is DERIVED from allowed_extensions, minus the types that are not text.
  // A second hand-kept list of "scannable" extensions is the shape that goes stale: the day a
  // caller adds `.svg` (or `.json`, or `.xml`) to its contract, a fixed list silently stops
  // covering the artifact it just approved — and svg in particular is markup that can carry a
  // script. Deriving it means a newly allowed text type is scanned the moment it is allowed.
  //
  // Files outside allowed_extensions are already a violation above, so nothing is lost by
  // scanning only allowed ones.
  // 🔴 `.svg` is deliberately NOT here even though it is an image for the 5a freeze below. SVG is
  // markup: it can hold a <script>, and therefore a directive. Classifying it by what it is used
  // for rather than by what it is made of would skip exactly the allowed type most likely to
  // carry executable text.
  const NOT_TEXT = new Set([
    ...[...IMAGE_EXTENSIONS].filter((e) => e !== '.svg'),
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.mp4', '.webm', '.mp3', '.wav',
    '.pdf', '.zip', '.gz', '.br', '.wasm',
  ])
  const scannable = files.filter((f) => {
    const ext = extname(f).toLowerCase()
    return allowed.has(ext) && !NOT_TEXT.has(ext)
  })
  for (const f of scannable) {
    if (SOURCE_MAPPING_DIRECTIVE.test(readFileSync(join(dist, f), 'utf8'))) {
      // Names the FILE, never the directive's value — that value IS the map.
      violations.push(
        `${f} carries a sourceMappingURL directive, so its source travels with it to whoever the ` +
          "bundle is served to. Set build.sourcemap=false; 'inline' and 'hidden' both defeat the " +
          '**/*.map filename ban.',
      )
    }
  }

  // 5a — image freeze by content hash.
  const inv = baseline.image_inventory
  if (inv && inv.mode === 'frozen') {
    const known = new Map(inv.entries.map((e) => [e.sha256, e]))
    const roots = [dist, ...(extraAssets && existsSync(extraAssets) ? [extraAssets] : [])]
    for (const root of roots) {
      for (const f of walk(root)) {
        if (!IMAGE_EXTENSIONS.has(extname(f).toLowerCase())) continue
        const digest = sha256(join(root, f))
        if (!known.has(digest)) {
          violations.push(
            `image not in the frozen inventory: ${f} (sha256 ${digest.slice(0, 16)}…). ` +
              'Add it deliberately with a reason. merglbot-public/website#470 shipped a named ' +
              "client's ad-performance screenshots at ordinary paths — the bytes were the leak, " +
              'not the filenames.',
          )
        }
      }
    }
  }

  if (violations.length > 0) {
    console.error(`✖ frontend-artifact-gate: ${violations.length} violation(s) in ${dist}`)
    for (const v of violations) fail(v)
    process.exit(EXIT_VIOLATION)
  }

  console.log(
    `✓ frontend-artifact-gate: ${files.length} files in ${dist} satisfy the contract ` +
      `(${baseline.banned_globs.length} banned patterns, ${baseline.required_paths.length} required paths)`,
  )
  process.exit(EXIT_OK)
}

main()
