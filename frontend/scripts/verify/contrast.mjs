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
const lum = (hex) =>
  srgb(hex)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4))
    .reduce((a, c, i) => a + c * [0.2126, 0.7152, 0.0722][i], 0);
const ratio = (a, b) => {
  const [l1, l2] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (l1 + 0.05) / (l2 + 0.05);
};

let failed = 0;
for (const [fg, bg, min, why] of PAIRS) {
  const r = ratio(TOKENS[fg], TOKENS[bg]);
  const ok = r >= min;
  if (!ok) failed++;
  console.log(
    `${ok ? "ok  " : "FAIL"}  ${fg} on ${bg}  ${r.toFixed(2)}:1 (need ${min}) — ${why}`,
  );
}
console.log(failed ? `\n${failed} contrast failures` : "\nall pairs pass");
process.exit(failed ? 1 : 0);
