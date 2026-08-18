import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

// marker is a highlighter: a background fill with ink on top. Used as a
// text colour it measures 2.46:1 on newsprint — failing AA body text and
// the 3:1 large-text threshold alike. This gate enforces what a contrast
// number cannot express: how the colour is used.
const FORBIDDEN = /(^|[\s"'`{])!?text-marker(\/\d+)?([\s"'`}]|$)/;

const ROOTS = ["src/pages", "src/components/marketing"];
const violations = [];
let scanned = 0;
const missing = [];

function walk(dir) {
  let entries;
  try {
    entries = readdirSync(dir);
  } catch {
    missing.push(dir);
    return;
  }
  for (const e of entries) {
    const full = join(dir, e);
    if (statSync(full).isDirectory()) walk(full);
    else if (/\.tsx?$/.test(e)) {
      scanned++;
      readFileSync(full, "utf8").split("\n").forEach((line, i) => {
        if (FORBIDDEN.test(line)) violations.push(`${full}:${i + 1}: ${line.trim()}`);
      });
    }
  }
}

for (const r of ROOTS) walk(r);

console.log(`scanned ${scanned} files across ${ROOTS.length - missing.length}/${ROOTS.length} roots`);
if (missing.length) console.log(`  not yet created (expected early in the plan): ${missing.join(", ")}`);

// Scanning nothing is never a pass. If every root is missing, the gate is
// blind and must say so rather than reporting success.
if (scanned === 0) {
  console.log("FAIL: scanned 0 files — the gate is blind, not clean");
  process.exit(1);
}

if (violations.length) {
  console.log(`\nFAIL: marker used as a text colour in ${violations.length} place(s):`);
  violations.forEach((v) => console.log(`  ${v}`));
  console.log("\nmarker is a fill. Use the `mark` token (bg-marker text-ink), never text-marker.");
  process.exit(1);
}

console.log("ok — marker is never used as a text colour");
