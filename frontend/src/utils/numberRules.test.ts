import { describe, expect, it } from "vitest";
import type { NumberRuleOut } from "../types/reservations";
import {
  canEditRule,
  emptyRuleForm,
  formatExpiry,
  formToCreate,
  formToPatch,
  normalizeE164,
  numberErrorKey,
  patchIsEmpty,
  ruleStatus,
  ruleToForm,
  showE164Hint,
} from "./numberRules";

const rule = (over: Partial<NumberRuleOut> = {}): NumberRuleOut => ({
  id: "r1",
  e164: "+12125550142",
  action: "block",
  location_id: "loc1",
  expires_at: null,
  expires_on: null,
  note: null,
  created_by_name: "Ada",
  created_at: "2026-11-18T15:00:00Z",
  active: true,
  ...over,
});

describe("number rules helpers", () => {
  it("accepts E.164 and tidies common separators", () => {
    expect(normalizeE164("+12125550142")).toBe("+12125550142");
    expect(normalizeE164(" +1 (212) 555-0142 ")).toBe("+12125550142");
    expect(normalizeE164("+44.20.7946.0958")).toBe("+442079460958");
  });

  it("rejects numbers without a + country code or with a leading zero", () => {
    expect(normalizeE164("2125550142")).toBeNull();
    expect(normalizeE164("+0212555")).toBeNull();
    expect(normalizeE164("+12345")).toBeNull(); // the server wants at least 7 digits
    expect(normalizeE164("+1234567890123456")).toBeNull();
    expect(normalizeE164("anonymous")).toBeNull();
  });

  it("shows the format hint only for a non-empty invalid number", () => {
    expect(showE164Hint("")).toBe(false);
    expect(showE164Hint("   ")).toBe(false);
    expect(showE164Hint("212-555")).toBe(true);
    expect(showE164Hint("+12125550142")).toBe(false);
  });

  it("reports the server's active flag as a status", () => {
    expect(ruleStatus(rule())).toBe("active");
    expect(ruleStatus(rule({ active: false }))).toBe("expired");
  });

  it("formats the expiry date without shifting it by the browser zone", () => {
    // npm test runs in Asia/Tokyo; a Date parsed from "2026-12-04" would still read Dec 4 there,
    // but the formatter must hold the calendar date in any zone.
    expect(formatExpiry("2026-12-04", "en-US", "Never")).toBe("Dec 4, 2026");
    expect(formatExpiry("2026-01-01", "en-US", "Never")).toBe("Jan 1, 2026");
    expect(formatExpiry(null, "en-US", "Never")).toBe("Never");
  });

  it("lets only admins change company-wide entries", () => {
    expect(canEditRule(false, rule())).toBe(true);
    expect(canEditRule(false, rule({ location_id: null }))).toBe(false);
    expect(canEditRule(true, rule({ location_id: null }))).toBe(true);
  });

  it("builds the create body from the form", () => {
    const form = { ...emptyRuleForm("loc1"), number: "+1 212 555 0142", note: "  Robocaller " };
    expect(formToCreate(form)).toEqual({
      e164: "+12125550142",
      action: "block",
      location_id: "loc1",
      expires_on: null,
      note: "Robocaller",
    });
    expect(formToCreate({ ...form, scope: "", expiresOn: "2026-12-04" })).toMatchObject({
      location_id: null,
      expires_on: "2026-12-04",
    });
    expect(formToCreate({ ...form, number: "555" })).toBeNull();
    expect(formToCreate({ ...form, note: "x".repeat(201) })).toBeNull();
  });

  it("edits only action, expiry and note", () => {
    const original = rule({ expires_on: "2026-12-04", note: "n", location_id: null });
    const form = ruleToForm(original);
    expect(form).toEqual({ number: "+12125550142", action: "block", scope: "", expiresOn: "2026-12-04", note: "n" });
    expect(formToPatch({ ...form, action: "allow", expiresOn: "" }, original)).toEqual({
      action: "allow",
      expires_on: null,
    });
  });

  it("sends only changed fields, so an expired entry keeps its past date", () => {
    const expired = rule({ active: false, expires_on: "2026-01-01", note: null });
    const patch = formToPatch({ ...ruleToForm(expired), note: " Old robocaller " }, expired)!;
    expect(patch).toEqual({ note: "Old robocaller" });
    expect(patchIsEmpty(patch)).toBe(false);
    expect(patchIsEmpty(formToPatch(ruleToForm(expired), expired)!)).toBe(true);
    expect(formToPatch({ ...ruleToForm(expired), note: "x".repeat(201) }, expired)).toBeNull();
  });

  it("maps number-list error codes to their messages", () => {
    expect(numberErrorKey("duplicate_rule")).toBe("duplicateRule");
    expect(numberErrorKey("no_caller_number")).toBe("noCallerNumber");
    expect(numberErrorKey("invalid_phone")).toBeNull();
    expect(numberErrorKey(null)).toBeNull();
    expect(numberErrorKey("number_allowed", { code: "number_allowed", scope: "here" })).toBe("numberAllowedHere");
    expect(numberErrorKey("number_allowed", { code: "number_allowed", scope: "all_locations" })).toBe(
      "numberAllowedAll",
    );
  });
});
