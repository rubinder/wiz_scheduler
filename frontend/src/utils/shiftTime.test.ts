import { describe, it, expect } from "vitest";
import { extractTime, extractOffset, formatTime } from "./shiftTime";

// The suite runs under TZ=Asia/Tokyo (see the "test" script in package.json).
// That is deliberate: every assertion below would still pass under a
// Date-based implementation if the runner's timezone happened to match the
// shift's offset. Running far from UTC is what gives these tests teeth.
describe("the test environment itself", () => {
  it("runs far from UTC, so a timezone conversion would be visible", () => {
    expect(new Date().getTimezoneOffset()).not.toBe(0);
  });
});

// A shift that genuinely starts at 9:00 AM at an America/New_York location.
const NY_9AM = "2026-08-31T09:00:00-04:00";

describe("formatTime", () => {
  it("reads the location's wall-clock face, not the viewer's timezone", () => {
    expect(formatTime(NY_9AM)).toBe("9:00 AM");
  });

  it("would disagree with a Date-based rendering, which is the bug (#92)", () => {
    // Documents why the assertion above is load-bearing: if anyone reverts
    // formatTime to `new Date(t).toLocaleTimeString(...)`, this is the value
    // it would produce here — so the test above fails rather than silently
    // agreeing.
    const viaDate = new Date(NY_9AM).toLocaleTimeString("en-US", {
      hour: "numeric",
      minute: "2-digit",
      hour12: true,
    });
    expect(viaDate).not.toBe("9:00 AM");
    expect(formatTime(NY_9AM)).not.toBe(viaDate);
  });

  it("renders the same face regardless of the offset the string carries", () => {
    // Same 9:00 face, four different locations. All must read as 9:00 AM.
    for (const offset of ["-04:00", "+00:00", "+09:00", "Z"]) {
      expect(formatTime(`2026-08-31T09:00:00${offset}`)).toBe("9:00 AM");
    }
  });

  it("handles a naive timestamp, as VACANT shifts carry", () => {
    // nodes.py builds VACANT shifts without an offset, so the grid renders
    // both shapes side by side and they must agree.
    expect(formatTime("2026-08-31T09:00:00")).toBe("9:00 AM");
    expect(formatTime("2026-08-31T09:00:00")).toBe(formatTime(NY_9AM));
  });

  it("handles bare HH:MM and HH:MM:SS, as shift templates carry", () => {
    expect(formatTime("09:00")).toBe("9:00 AM");
    expect(formatTime("17:30:00")).toBe("5:30 PM");
  });

  it("renders midnight and noon correctly", () => {
    expect(formatTime("2026-08-31T00:00:00-04:00")).toBe("12:00 AM");
    expect(formatTime("2026-08-31T12:00:00-04:00")).toBe("12:00 PM");
    expect(formatTime("2026-08-31T23:59:00-04:00")).toBe("11:59 PM");
  });

  it("returns the input unchanged when no hour can be read", () => {
    expect(formatTime("")).toBe("");
    expect(formatTime("not a time")).toBe("not a time");
  });
});

describe("extractTime", () => {
  it("takes the face from either shape", () => {
    expect(extractTime(NY_9AM)).toBe("09:00");
    expect(extractTime("2026-08-31T09:00:00")).toBe("09:00");
    expect(extractTime("09:00")).toBe("09:00");
    expect(extractTime("09:00:00")).toBe("09:00");
  });
});

describe("extractOffset", () => {
  it("recovers the offset so an edit can put it back", () => {
    expect(extractOffset(NY_9AM)).toBe("-04:00");
    expect(extractOffset("2026-08-31T09:00:00+09:00")).toBe("+09:00");
    expect(extractOffset("2026-08-31T09:00:00Z")).toBe("Z");
  });

  it("returns empty for a naive timestamp rather than inventing one", () => {
    expect(extractOffset("2026-08-31T09:00:00")).toBe("");
    expect(extractOffset("09:00")).toBe("");
  });

  it("round-trips: rebuilding an edited time preserves the instant", () => {
    // What the edit modals do: read the face, let the manager change it,
    // reattach the original offset. Dropping the offset here would silently
    // re-anchor the shift to a different instant.
    const rebuilt = `2026-08-31T10:30:00${extractOffset(NY_9AM)}`;
    expect(rebuilt).toBe("2026-08-31T10:30:00-04:00");
    expect(formatTime(rebuilt)).toBe("10:30 AM");
  });
});
