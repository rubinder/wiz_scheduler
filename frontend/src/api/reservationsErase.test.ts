/**
 * eraseDinerDataByCode (#259 launch task 5): erasure of one reservation by its
 * confirmation code posts the code in the body, never in the URL.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { eraseDinerDataByCode } from "./reservations";

describe("eraseDinerDataByCode", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("localStorage", { getItem: () => "tok", setItem: () => {}, removeItem: () => {} });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("posts the code to the location's erase-by-code route", async () => {
    const out = {
      reservations_scrubbed: 1,
      external_bookings_deleted: 0,
      conversations_deleted: 2,
      conversations_closed_in_flight: 0,
      conversations_still_in_flight: 0,
    };
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(out), { status: 200, headers: { "Content-Type": "application/json" } }),
    );
    await expect(eraseDinerDataByCode("loc12345", "ab2c3d")).resolves.toEqual(out);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/reservations\/locations\/loc12345\/diner-data\/erase-by-code$/);
    expect(String(url)).not.toContain("ab2c3d");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ code: "ab2c3d" });
  });
});
