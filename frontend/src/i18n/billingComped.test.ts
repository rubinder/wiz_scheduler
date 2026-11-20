/**
 * A comped AI host group (#259) sees its plan in the price line and its
 * status as "Included": the two must not say the same thing twice.
 */
import { describe, expect, it } from "vitest";
import { LANGUAGES } from "./types";

const MODULES = import.meta.glob("./*.ts", { import: "default", eager: true }) as Record<string, unknown>;

type Billing = { compedPrice?: string; statuses: { comped: string } };

describe("comped billing copy", () => {
  for (const { code } of LANGUAGES) {
    it(`${code}: the price line has its own comped string`, () => {
      const t = MODULES[`./${code}.ts`] as { reservations: { billing: Billing } };
      const b = t.reservations.billing;
      expect(b.compedPrice).toBeTruthy();
      expect(b.compedPrice).not.toBe(b.statuses.comped);
    });
  }
});
