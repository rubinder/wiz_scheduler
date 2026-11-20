/**
 * Launch copy for the AI host (#259 step 11) in every locale: the demo
 * restaurant's banner, the comped plan's price line, the comped cap error,
 * and the greeting hint, which must name only the word "automated" (the
 * server no longer accepts "assistant" alone).
 */
import { describe, expect, it } from "vitest";
import en from "./en";
import { LANGUAGES } from "./types";

const MODULES = import.meta.glob("./*.ts", { import: "default", eager: true }) as Record<string, unknown>;

type Reservations = {
  publicChat?: { demoBanner?: string };
  billing: { compedPrice: string };
  errors: Record<string, string>;
  rules: { greetingHint: string };
};

describe("AI host launch copy", () => {
  it("en: the exact banner and price line", () => {
    const r = en.reservations as unknown as Reservations;
    expect(r.publicChat?.demoBanner).toBe(
      "This is a demo restaurant. Please use made-up details — the demo account can see what you type.",
    );
    expect(r.billing.compedPrice).toBe("Complimentary plan");
  });

  for (const { code } of LANGUAGES) {
    it(`${code}: banner, comped error and a hint naming only "automated"`, () => {
      const r = (MODULES[`./${code}.ts`] as { reservations: Reservations }).reservations;
      expect(r.publicChat?.demoBanner).toBeTruthy();
      expect(r.errors.comped_cap_limit).toContain("{cap}");
      expect(r.rules.greetingHint).toContain('"automated"');
      expect(r.rules.greetingHint).not.toContain('"assistant"');
      expect(r.billing.compedPrice.toLowerCase()).not.toContain("demo");
    });
  }
});
