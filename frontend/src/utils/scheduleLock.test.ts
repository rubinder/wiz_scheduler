import { describe, expect, it } from "vitest";
import en from "../i18n/en";
import { scheduleLockedErrorFromDetail } from "../hooks/useScheduleStream";
import { scheduleLockedMessage } from "./scheduleLock";

const modal = (name: string, location?: string | null, shared = false) =>
  scheduleLockedMessage(
    en.approvedSchedules.scheduleLockedBody,
    en.approvedSchedules.scheduleLockedBodyLocation,
    { name },
    location,
    en.approvedSchedules.scheduleLockedBodyShared,
    shared,
  );

const toast = (lockedBy: string, countdown: string, location?: string | null, shared = false) =>
  scheduleLockedMessage(
    en.schedule.lockedToastBody,
    en.schedule.lockedToastBodyHere,
    { locked_by: lockedBy, countdown },
    location,
    en.schedule.lockedToastBodyLocation,
    shared,
  );

describe("schedule lock message", () => {
  it("names the location when the 409 carries one", () => {
    expect(modal("Ben Boss", "Bravo")).toBe("Ben Boss is scheduling Bravo. Try again shortly.");
    expect(toast("Ben Boss", "01:05", "Bravo")).toBe(
      "Ben Boss is already scheduling Bravo. Try again in 01:05.",
    );
  });

  it("says the location shares employees only when the server says so (#222)", () => {
    expect(modal("Ben Boss", "Bravo", true)).toBe(
      "Ben Boss is scheduling Bravo, which shares employees with this schedule. Try again shortly.",
    );
    expect(toast("Ben Boss", "01:05", "Bravo", true)).toBe(
      "Ben Boss is scheduling Bravo, which shares employees with this schedule. Try again in 01:05.",
    );
  });

  it("falls back to the existing text without a location", () => {
    expect(modal("Ben Boss")).toBe("Ben Boss is currently editing this schedule. Try again shortly.");
    expect(modal("Ben Boss", null)).toBe(modal("Ben Boss"));
    expect(toast("Ben Boss", "00:30", "")).toBe(
      "Ben Boss is generating or approving a schedule. Try again in 00:30.",
    );
  });

  it("leaves no placeholder behind", () => {
    expect(modal("A", "B")).not.toMatch(/\{\w+\}/);
    expect(toast("A", "00:01", "B")).not.toMatch(/\{\w+\}/);
  });
});

describe("scheduleLockedErrorFromDetail", () => {
  it("reads location_name from the 409 detail", () => {
    const err = scheduleLockedErrorFromDetail({
      locked_by: "Ben Boss",
      expires_at: "2026-10-27T12:00:00+00:00",
      location_name: "Bravo",
    });
    expect(err.lockedBy).toBe("Ben Boss");
    expect(err.locationName).toBe("Bravo");
    expect(err.sharedStaff).toBe(false);
    expect(err.expiresAt.toISOString()).toBe("2026-10-27T12:00:00.000Z");
  });

  it("reads shared_staff from the 409 detail (#222)", () => {
    const err = scheduleLockedErrorFromDetail({
      locked_by: "Ben Boss", expires_at: "2026-10-27T12:00:00+00:00",
      location_name: "Bravo", shared_staff: true,
    });
    expect(err.sharedStaff).toBe(true);
  });

  it("keeps working with an older detail that has no location", () => {
    const err = scheduleLockedErrorFromDetail({ expires_at: "2026-10-27T12:00:00+00:00" });
    expect(err.lockedBy).toBe("another manager");
    expect(err.locationName).toBeNull();
  });
});

const LOCALES = import.meta.glob(
  ["../i18n/*.ts", "!../i18n/*.test.ts", "!../i18n/types.ts", "!../i18n/en.ts"],
  { import: "default", eager: true },
) as Record<
  string,
  { schedule?: Record<string, string>; approvedSchedules?: Record<string, string> }
>;

const LOCK_KEYS = [
  ["schedule", "lockedToastTitle"],
  ["schedule", "lockedToastBody"],
  ["schedule", "lockedToastBodyLocation"],
  ["schedule", "lockedToastBodyHere"],
  ["approvedSchedules", "scheduleLockedBodyLocation"],
  ["approvedSchedules", "scheduleLockedBodyShared"],
] as const;

const placeholders = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();

describe("schedule lock strings in every locale", () => {
  const locales = Object.entries(LOCALES).filter(([, t]) => t && typeof t === "object" && "schedule" in t);

  it("covers the 18 non-English locales", () => {
    expect(locales.length).toBe(18);
  });

  for (const [path, t] of locales) {
    it(`${path} translates them with the same placeholders`, () => {
      for (const [section, key] of LOCK_KEYS) {
        const english = en[section][key as keyof (typeof en)[typeof section]] as string;
        const value = t[section]?.[key];
        expect(value, `${section}.${key}`).toBeTruthy();
        expect(value, `${section}.${key}`).not.toBe(english);
        expect(placeholders(value!)).toEqual(placeholders(english));
      }
    });
  }
});
