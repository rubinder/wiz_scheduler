import { describe, expect, it } from "vitest";
import {
  browserTimeZone,
  employeeLocationZones,
  locationZoneNote,
  timeZoneOptions,
} from "./timezones";

describe("browserTimeZone", () => {
  it("reads the runtime zone (the test script pins TZ=Asia/Tokyo)", () => {
    expect(browserTimeZone()).toBe("Asia/Tokyo");
  });
});

describe("timeZoneOptions", () => {
  it("puts the browser zone first and includes UTC", () => {
    const opts = timeZoneOptions();
    expect(opts[0].value).toBe("Asia/Tokyo");
    expect(opts.some((o) => o.value === "UTC")).toBe(true);
  });

  it("keeps a current value even if the runtime list lacks it", () => {
    expect(timeZoneOptions("Etc/GMT+5").some((o) => o.value === "Etc/GMT+5")).toBe(true);
  });

  it("has no duplicates", () => {
    const values = timeZoneOptions("Asia/Tokyo").map((o) => o.value);
    expect(new Set(values).size).toBe(values.length);
  });

  it("labels with spaces instead of underscores", () => {
    expect(timeZoneOptions("America/New_York").find((o) => o.value === "America/New_York")?.label)
      .toBe("America/New York");
  });
});

describe("locationZoneNote", () => {
  it("names the location zone: availability is always location-local (#171)", () => {
    expect(locationZoneNote(["America/Los_Angeles"])).toBe("America/Los Angeles");
  });
  it("lists each distinct zone once", () => {
    expect(locationZoneNote(["America/New_York", "America/Los_Angeles", "America/New_York"]))
      .toBe("America/New York, America/Los Angeles");
  });
  it("is null when no zone is known", () => {
    expect(locationZoneNote([])).toBeNull();
  });
});

describe("employeeLocationZones", () => {
  const zones = new Map([
    ["loc-ny", "America/New_York"],
    ["loc-la", "America/Los_Angeles"],
  ]);
  it("maps the employee's location ids to their zones", () => {
    expect(employeeLocationZones(["loc-la", "loc-ny"], zones)).toEqual([
      "America/Los_Angeles",
      "America/New_York",
    ]);
  });
  it("skips unknown locations and handles no locations", () => {
    expect(employeeLocationZones(["loc-gone"], zones)).toEqual([]);
    expect(employeeLocationZones(null, zones)).toEqual([]);
  });
});
