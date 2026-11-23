/**
 * saveFlows (decision 102): the flow page sends only the flows (and, for an
 * admin, the flow editors) to its own route, never the whole rules, so it
 * cannot revert a colleague's rules edit.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { saveFlows } from "./reservations";

describe("saveFlows", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal("localStorage", { getItem: () => "tok", setItem: () => {}, removeItem: () => {} });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  it("puts only the flows and editors to the location's flows route", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify({ rules: {}, version: 3 }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    const body = { flows: { web: null, voice: null }, flow_editor_ids: ["abcd1234"] };
    await saveFlows("loc12345", body);
    const [url, init] = fetchMock.mock.calls[0];
    expect(String(url)).toMatch(/\/reservations\/locations\/loc12345\/flows$/);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual(body);
  });
});
