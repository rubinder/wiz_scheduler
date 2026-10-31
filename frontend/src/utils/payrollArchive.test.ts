import { describe, expect, it } from "vitest";
import { formatArchivePrice } from "./payrollArchive";

describe("formatArchivePrice", () => {
  it("drops cents for a whole-dollar price", () => {
    expect(formatArchivePrice(18)).toBe("$18");
  });
  it("keeps cents otherwise", () => {
    expect(formatArchivePrice(18.5)).toBe("$18.50");
  });
});
