import { describe, it, expect } from "vitest";
import { marketing as m } from "../theme";
import { signInLinkClass } from "./registerNudge";

describe("signInLinkClass", () => {
  it("keeps the normal link size when Google sign-up hasn't happened", () => {
    const cls = signInLinkClass(false);
    expect(cls).not.toContain("text-[1.75rem]");
    expect(cls).toContain(m.btn.link);
  });

  it("doubles the link size once the visitor picks Google, as a nudge toward /login (#110)", () => {
    const cls = signInLinkClass(true);
    expect(cls).toContain("text-[1.75rem]");
    expect(cls).toContain(m.btn.link);
  });
});
