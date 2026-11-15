/**
 * GET /export/jsonl/{id} through the api layer (#251). The project has no
 * DOM test environment, so the request is checked against a stubbed fetch.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import { downloadScheduleJsonl } from "./exportSchedules";

const fetchMock = vi.fn();
let store: Record<string, string>;

function fileResponse(body: string, disposition: string | null): Response {
  const headers = new Headers({ "Content-Type": "application/x-ndjson" });
  if (disposition) headers.set("Content-Disposition", disposition);
  return new Response(body, { status: 200, headers });
}

beforeEach(() => {
  store = { token: "tok" };
  vi.stubGlobal("localStorage", { getItem: (k: string) => store[k] ?? null });
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  fetchMock.mockReset();
});

describe("downloadScheduleJsonl", () => {
  it("sends the bearer token in a header, never in the URL", async () => {
    fetchMock.mockResolvedValue(fileResponse("{}\n", null));
    await downloadScheduleJsonl("abc12345");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/export/jsonl/abc12345");
    expect(init.headers).toEqual({ Authorization: "Bearer tok" });
  });

  it("omits the Authorization header when signed out", async () => {
    store = {};
    fetchMock.mockResolvedValue(fileResponse("{}\n", null));
    await downloadScheduleJsonl("abc12345");
    expect(fetchMock.mock.calls[0][1].headers).toEqual({});
  });

  it("uses the server's filename and returns the file body", async () => {
    fetchMock.mockResolvedValue(
      fileResponse('{"a":1}\n', 'attachment; filename="schedule_abc12345_Main_St.jsonl"'),
    );
    const { blob, filename } = await downloadScheduleJsonl("abc12345");
    expect(filename).toBe("schedule_abc12345_Main_St.jsonl");
    expect(await blob.text()).toBe('{"a":1}\n');
  });

  it("falls back to schedule_<id>.jsonl without a Content-Disposition", async () => {
    fetchMock.mockResolvedValue(fileResponse("{}\n", null));
    const { filename } = await downloadScheduleJsonl("abc12345");
    expect(filename).toBe("schedule_abc12345.jsonl");
  });

  it("throws an ApiError carrying the backend's detail", async () => {
    fetchMock.mockResolvedValue(
      new Response(JSON.stringify({ detail: "Approved schedule not found" }), { status: 404 }),
    );
    const err = await downloadScheduleJsonl("nope").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(404);
    expect((err as ApiError).message).toBe("Approved schedule not found");
  });
});
