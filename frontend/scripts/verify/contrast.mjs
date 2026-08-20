const TOKENS = {
  ink: "#1A1815",
  newsprint: "#E9E4D8",
  paper: "#F5F2EA",
  rule: "#C9C0B0",
  marker: "#FF5A1F",
  clear: "#1D7357",
};

// [foreground, background, minimum ratio, why]
const PAIRS = [
  ["ink", "newsprint", 4.5, "body text on page ground"],
  ["ink", "paper", 4.5, "body text on raised surfaces"],
  ["clear", "paper", 4.5, "compliant/success text on the receipt"],
  ["clear", "newsprint", 4.5, "compliant/success text on page ground"],
  // marker is a FILL, never a text colour — so the pair that matters is
  // ink sitting ON marker, not marker sitting on the page.
  ["ink", "marker", 4.5, "ink text on a marker highlight fill"],
  ["marker", "newsprint", 1.3, "decorative rule only — must be visible, never load-bearing"],
  ["marker", "ink", 3.0, "accent on the dark demo band"],
  ["newsprint", "ink", 4.5, "reversed text on the dark demo band"],
  ["rule", "newsprint", 1.3, "grid lines must be visible, not invisible"],
];

const srgb = (hex) => [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255);
const lumRgb = (rgb) =>
  rgb
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
    .reduce((a, c, i) => a + c * [0.2126, 0.7152, 0.0722][i], 0);
const lum = (hex) => lumRgb(srgb(hex));
const ratioRgb = (rgb1, rgb2) => {
  const [l1, l2] = [lumRgb(rgb1), lumRgb(rgb2)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
};
const ratio = (a, b) => ratioRgb(srgb(a), srgb(b));

// Alpha compositing: browsers blend rgba() colours directly in encoded
// (gamma) sRGB space, not in linear light — so this must match, not
// linearize-then-blend. `fgHex/NN` (e.g. text-ink/60) composited over an
// OPAQUE background is what a reader's eye actually sees; testing the
// opaque foreground alone (as the plain PAIRS above do) is blind to this.
const compositeOverBg = (fgHex, bgHex, alpha) => {
  const fg = srgb(fgHex);
  const bg = srgb(bgHex);
  return fg.map((c, i) => c * alpha + bg[i] * (1 - alpha));
};
const ratioAlpha = (fgHex, alpha, bgHex) =>
  ratioRgb(compositeOverBg(fgHex, bgHex, alpha), srgb(bgHex));

let failed = 0;
for (const [fg, bg, min, why] of PAIRS) {
  const r = ratio(TOKENS[fg], TOKENS[bg]);
  const ok = r >= min;
  if (!ok) failed++;
  console.log(
    `${ok ? "ok  " : "FAIL"}  ${fg} on ${bg}  ${r.toFixed(2)}:1 (need ${min}) — ${why}`,
  );
}

// [foreground, alpha, background, minimum ratio, why, blocking]
//
// `blocking: true`  — this alpha value is live in theme.ts as text. A
//                      regression here must fail the build.
// `blocking: false` — audit/canary entries. They prove *why* an alpha
//                      level is unusable as text; they are expected to
//                      keep failing forever and are not a bug to fix here.
//                      Per the review that added this section: report
//                      them, do not loosen the 4.5 threshold to silence
//                      them, and do not let them block the gate — nothing
//                      in the live theme renders text at ink/60 or ink/50,
//                      and ink/40 is a deliberately-subordinate placeholder
//                      hint (the real label already carries the AA-passing
//                      copy via ink/70), not the primary text it sits in.
const ALPHA_PAIRS = [
  ["ink", 0.7, "newsprint", 4.5, "ink/70 (muted body / meta / label) on page ground", true],
  ["ink", 0.7, "paper", 4.5, "ink/70 (muted body / meta / label) on raised surfaces", true],
  ["ink", 0.6, "newsprint", 4.5, "ink/60 as text on page ground — must never be used for text", false],
  ["ink", 0.6, "paper", 4.5, "ink/60 as text on raised surfaces — must never be used for text", false],
  ["ink", 0.5, "newsprint", 4.5, "ink/50 as text on page ground — must never be used for text", false],
  ["ink", 0.5, "paper", 4.5, "ink/50 as text on raised surfaces — must never be used for text", false],
  ["ink", 0.4, "newsprint", 4.5, "ink/40 (placeholder hint text) on page ground — known non-AA exception", false],
  ["ink", 0.4, "paper", 4.5, "ink/40 (placeholder hint text) on raised surfaces — known non-AA exception", false],
];

let advisory = 0;
for (const [fg, alpha, bg, min, why, blocking] of ALPHA_PAIRS) {
  const r = ratioAlpha(TOKENS[fg], alpha, TOKENS[bg]);
  const ok = r >= min;
  if (!ok) {
    if (blocking) failed++;
    else advisory++;
  }
  const label = ok ? "ok  " : blocking ? "FAIL" : "info";
  console.log(
    `${label}  ${fg}/${alpha * 100} on ${bg}  ${r.toFixed(2)}:1 (need ${min}) — ${why}`,
  );
}

console.log(failed ? `\n${failed} contrast failures` : "\nall blocking pairs pass");
if (advisory) {
  console.log(
    `${advisory} advisory pair(s) fail (ink/60, ink/50, ink/40 as text) — expected, not blocking, ` +
      `not fixed by loosening the threshold; nothing in the live theme renders body text at these alpha levels.`,
  );
}
process.exit(failed ? 1 : 0);
