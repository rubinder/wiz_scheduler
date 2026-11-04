/**
 * Mechanical layering rules from .claude/skills/architecture/SKILL.md.
 * Existing violations live in architecture_allowlist.json; the list may only shrink.
 */
import { describe, expect, it } from "vitest";

// Vite's glob import rather than node:fs so the file type-checks under the
// same tsconfig as the app (no @types/node) and `npm run build` stays green.
// Keys look like "./api/client.ts"; test files are dropped below.
const SOURCES = import.meta.glob(["./**/*.ts", "./**/*.tsx"], {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;
const ALLOWLIST = (
  import.meta.glob("./architecture_allowlist.json", { query: "?raw", import: "default", eager: true }) as Record<string, string>
)["./architecture_allowlist.json"];

type Hit = { rule: string; file: string; detail: string };

// `new Date("2026-11-04")`: a date-only string parses as UTC midnight.
const DATE_ONLY = /new Date\(\s*["'`]\d{4}-\d{2}-\d{2}["'`]\s*\)/g;
// Availability is evaluated only by services/availability_check.is_available (CLAUDE.md).
const AVAIL_EVAL = /\b(isAvailable|is_available|checkAvailability)\s*\(/g;

/** `sources` maps a path relative to src/ (e.g. "api/client.ts") to its text. */
export function scan(sources: Record<string, string>): Hit[] {
  const hits: Hit[] = [];
  for (const [rel, text] of Object.entries(sources)) {
    if (!rel.startsWith("api/") && /\bfetch\(/.test(text)) hits.push({ rule: "fetch-outside-api", file: rel, detail: "fetch(" });
    for (const m of text.match(DATE_ONLY) ?? []) hits.push({ rule: "date-only-string-parse", file: rel, detail: m });
    for (const m of text.match(AVAIL_EVAL) ?? []) hits.push({ rule: "frontend-availability-eval", file: rel, detail: m.replace(/\s*\($/, "") });
  }
  const seen = new Set<string>();
  return hits.filter((h) => { const k = key(h); return seen.has(k) ? false : (seen.add(k), true); });
}

function key(h: Hit): string {
  return `${h.rule}|${h.file}|${h.detail}`;
}

describe("frontend architecture", () => {
  const production: Record<string, string> = {};
  for (const [path, text] of Object.entries(SOURCES)) {
    if (!/\.test\.tsx?$/.test(path)) production[path.replace(/^\.\//, "")] = text;
  }
  const found = new Set(scan(production).map(key));
  const allowlist = JSON.parse(ALLOWLIST) as Array<Hit & { issue: number | null }>;
  const allowed = new Set(allowlist.map(key));

  it("has no new violations", () => {
    expect([...found].filter((k) => !allowed.has(k))).toEqual([]);
  });

  it("allowlist only shrinks (delete entries for fixed violations)", () => {
    expect([...allowed].filter((k) => !found.has(k))).toEqual([]);
  });

  it("catches a planted violation of every rule", () => {
    const rules = scan({
      "pages/X.tsx": 'fetch("/api"); const d = new Date("2026-11-04"); const ok = isAvailable(emp, shift);',
    })
      .map((h) => h.rule)
      .sort();
    expect(rules).toEqual(["date-only-string-parse", "fetch-outside-api", "frontend-availability-eval"]);
  });

  it("allows fetch inside api/", () => {
    expect(scan({ "api/client.ts": 'fetch("/api");' })).toEqual([]);
  });
});
