/**
 * RTL guard: no physical-direction Tailwind utilities in components (#93).
 *
 * `ar` and `ur` are RTL, and LanguageContext sets document.dir from the
 * active locale. A physical utility (`text-left`, `sticky left-0`, `ml-2`)
 * therefore pins to the wrong edge in those two locales while looking
 * correct in the other seventeen — which is exactly why this kind of bug
 * survives review. The logical forms are identical in LTR, so the swap is
 * invisible except where it matters.
 *
 * A test rather than a lint rule because the project has no lint script;
 * this runs in the same `npm test` everything else does.
 */
import { describe, expect, it } from "vitest";

// Vite's glob import rather than node:fs so the file type-checks under the
// same tsconfig as the app (no @types/node) and `npm run build` stays green.
const COMPONENTS = import.meta.glob("../**/*.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

/** [pattern, what to use instead] */
const BANNED: [RegExp, string][] = [
  [/\btext-left\b/, "text-start"],
  [/\btext-right\b/, "text-end"],
  [/\bml-(?:auto|px|full|\d+(?:\.\d+)?|\[[^\]]*\])\b/, "ms-*"],
  [/\bmr-(?:auto|px|full|\d+(?:\.\d+)?|\[[^\]]*\])\b/, "me-*"],
  [/\bpl-(?:auto|px|full|\d+(?:\.\d+)?|\[[^\]]*\])\b/, "ps-*"],
  [/\bpr-(?:auto|px|full|\d+(?:\.\d+)?|\[[^\]]*\])\b/, "pe-*"],
  [/\bborder-l\b/, "border-s"],
  [/\bborder-r\b/, "border-e"],
  [/\bleft-0\b/, "start-0"],
  [/\bright-0\b/, "end-0"],
];

describe("logical direction utilities", () => {
  it("no component uses a physical-direction utility", () => {
    const offenders: string[] = [];

    for (const [file, source] of Object.entries(COMPONENTS)) {
      const rel = file.replace(/^\.\.\//, "");
      source.split("\n").forEach((line, i) => {
        for (const [pattern, fix] of BANNED) {
          if (pattern.test(line)) {
            offenders.push(`${rel}:${i + 1}: use ${fix} — ${line.trim()}`);
          }
        }
      });
    }

    expect(offenders).toEqual([]);
  });

  it("does not flag colour classes that merely start with a banned prefix", () => {
    // border-red-500 and border-rule both begin with "border-r". A naive
    // prefix match corrupts them; this is why every pattern is anchored.
    const decoys = [
      'className="border border-red-300 bg-red-50"',
      'className="border-rule text-ink"',
      'className="ms-2 text-start"',
    ];
    for (const decoy of decoys) {
      expect(BANNED.some(([p]) => p.test(decoy))).toBe(false);
    }
  });
});
