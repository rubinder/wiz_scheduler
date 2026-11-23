import { describe, expect, it } from "vitest";
import {
  capacityRequestBody,
  capacityRequestProblem,
  emptyCapacityForm,
  transferNumberBody,
  transferNumberProblem,
} from "./voiceCapacity";

const valid = () => ({
  ...emptyCapacityForm(),
  locationIds: ["loc1"],
  expectedPeak: "40",
  neededBy: "2026-12-15",
  note: "  Holiday rush  ",
});

describe("capacity request form", () => {
  it("starts empty with every voice location ticked", () => {
    expect(emptyCapacityForm(["a", "b"])).toEqual({
      locationIds: ["a", "b"],
      expectedPeak: "",
      neededBy: "",
      note: "",
    });
  });

  it("accepts a complete form", () => {
    expect(capacityRequestProblem(valid(), 500)).toBeNull();
  });

  it("needs at least one location", () => {
    expect(capacityRequestProblem({ ...valid(), locationIds: [] }, 500)).toBe("locations");
  });

  it("needs a whole number of calls from 1 to the maximum", () => {
    for (const bad of ["", "0", "501", "2.5", "abc", "-3"]) {
      expect(capacityRequestProblem({ ...valid(), expectedPeak: bad }, 500)).toBe("peak");
    }
    expect(capacityRequestProblem({ ...valid(), expectedPeak: "500" }, 500)).toBeNull();
    expect(capacityRequestProblem({ ...valid(), expectedPeak: "1" }, 500)).toBeNull();
  });

  it("caps the note at 1,000 characters", () => {
    expect(capacityRequestProblem({ ...valid(), note: "x".repeat(1001) }, 500)).toBe("note");
    expect(capacityRequestProblem({ ...valid(), note: "x".repeat(1000) }, 500)).toBeNull();
  });

  it("builds the request body, keeping the needed-by date as its YYYY-MM-DD string", () => {
    expect(capacityRequestBody(valid())).toEqual({
      location_ids: ["loc1"],
      expected_peak: 40,
      needed_by: "2026-12-15",
      note: "Holiday rush",
    });
    expect(capacityRequestBody({ ...valid(), neededBy: "" }).needed_by).toBeNull();
  });
});

describe("company staff line", () => {
  it("accepts an E.164 number or nothing", () => {
    expect(transferNumberProblem("+12125550123")).toBe(false);
    expect(transferNumberProblem("  ")).toBe(false);
    expect(transferNumberProblem("2125550123")).toBe(true);
    expect(transferNumberProblem("+1 212 555")).toBe(true);
  });

  it("sends a blank number as null", () => {
    expect(transferNumberBody(" +12125550123 ")).toEqual({ default_staff_transfer_number: "+12125550123" });
    expect(transferNumberBody("")).toEqual({ default_staff_transfer_number: null });
  });
});
