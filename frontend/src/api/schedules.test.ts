/**
 * POST /schedules/generate through the api layer (#238). The project has no
 * DOM test environment, so the request is checked against a stubbed fetch.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "./client";
import {
  approveSchedule,
  generateScheduleBody,
  generateScheduleStream,
  ScheduleLockedError,
} from "./schedules";

describe("generateScheduleBody", () => {
  it("sends only the week when nothing else is given", () => {
    expect(generateScheduleBody("2026-11-09")).toEqual({ week_start_date: "2026-11-09" });
  });

  it("leaves out empty id lists and includes non-empty ones", () => {
    expect(generateScheduleBody("w", [], [])).toEqual({ week_start_date: "w" });
    expect(generateScheduleBody("w", ["loc1"], ["tpl1", "tpl2"])).toEqual({
      week_start_date: "w",
      location_ids: ["loc1"],
      template_ids: ["tpl1", "tpl2"],
    });
  });

  it("adds local-mode fields, defaulting the strategy to random", () => {
    expect(generateScheduleBody("w", undefined, undefined, { useLocal: true })).toEqual({
      week_start_date: "w",
      use_local: true,
      strategy: "random",
    });
    expect(
      generateScheduleBody("w", undefined, undefined, {
        useLocal: true,
        strategy: "rotation",
        strategyParam: 3,
        strategyParam2: 0,
      }),
    ).toEqual({
      week_start_date: "w",
      use_local: true,
      strategy: "rotation",
      strategy_param: 3,
      strategy_param2: 0,
    });
  });

  it("leaves out strategy fields without useLocal", () => {
    expect(
      generateScheduleBody("w", undefined, undefined, { strategy: "max_hours", strategyParam: 2 }),
    ).toEqual({ week_start_date: "w" });
  });

  it("sends num_days only when it is not 7", () => {
    expect(generateScheduleBody("w", undefined, undefined, {})).not.toHaveProperty("num_days");
    expect(generateScheduleBody("w", undefined, undefined, { numDays: 7 })).not.toHaveProperty("num_days");
    expect(generateScheduleBody("w", undefined, undefined, { numDays: 5 })).toEqual({
      week_start_date: "w",
      num_days: 5,
    });
  });

  it("sends every defined weight, including 0", () => {
    expect(
      generateScheduleBody("w", undefined, undefined, {
        seniorityWeight: 0,
        payWeight: 0.5,
        overtimeWeight: 1,
        affinityWeight: 0,
      }),
    ).toEqual({
      week_start_date: "w",
      seniority_weight: 0,
      pay_weight: 0.5,
      overtime_weight: 1,
      affinity_weight: 0,
    });
  });
});

describe("generate stream and approve over a stubbed fetch", () => {
  const fetchMock = vi.fn();
  let store: Record<string, string>;
  const body = { week_start_date: "2026-11-09" };
  const signal = () => new AbortController().signal;

  beforeEach(() => {
    store = { token: "tok" };
    vi.stubGlobal("localStorage", { getItem: (k: string) => store[k] ?? null });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  const respondJson = (status: number, json: unknown, statusText = "") =>
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(json), {
        status,
        statusText,
        headers: { "Content-Type": "application/json" },
      }),
    );
  const textOf = (s: ReadableStream<Uint8Array>) => new Response(s).text();
  const caught = (p: Promise<unknown>) => p.then(() => null, (e: unknown) => e);

  it("POSTs the body with the token, JSON content type and the caller's signal", async () => {
    fetchMock.mockResolvedValueOnce(new Response("{}\n"));
    const controller = new AbortController();
    await generateScheduleStream(body, controller.signal);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/schedules/generate");
    expect(init.method).toBe("POST");
    expect(init.headers).toEqual({ "Content-Type": "application/json", Authorization: "Bearer tok" });
    expect(JSON.parse(init.body)).toEqual(body);
    expect(init.signal).toBe(controller.signal);
  });

  it("sends no Authorization header without a token", async () => {
    delete store.token;
    fetchMock.mockResolvedValueOnce(new Response("{}\n"));
    await generateScheduleStream(body, signal());
    expect(fetchMock.mock.calls[0][1].headers).not.toHaveProperty("Authorization");
  });

  it("resolves to the response body as a stream", async () => {
    const ndjson = '{"location_id":"a"}\n{"location_id":"b"}\n';
    fetchMock.mockResolvedValueOnce(new Response(ndjson));
    expect(await textOf(await generateScheduleStream(body, signal()))).toBe(ndjson);
  });

  it("passes a multibyte name split across chunks through untouched", async () => {
    const line = '{"location_name":"東京"}\n';
    const bytes = new TextEncoder().encode(line);
    // byte 20 falls inside the 3-byte encoding of 東 (bytes 18-20)
    const chunked = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(bytes.slice(0, 20));
        c.enqueue(bytes.slice(20));
        c.close();
      },
    });
    fetchMock.mockResolvedValueOnce(new Response(chunked));
    expect(await textOf(await generateScheduleStream(body, signal()))).toBe(line);
  });

  it("turns a 409 schedule_locked into ScheduleLockedError", async () => {
    respondJson(409, {
      detail: {
        code: "schedule_locked",
        locked_by: "Ada",
        expires_at: "2026-11-09T10:00:00Z",
        location_name: "Shibuya",
        shared_staff: true,
      },
    });
    const err = await caught(generateScheduleStream(body, signal()));
    expect(err).toBeInstanceOf(ScheduleLockedError);
    const locked = err as ScheduleLockedError;
    expect(locked.lockedBy).toBe("Ada");
    expect(locked.expiresAt.toISOString()).toBe("2026-11-09T10:00:00.000Z");
    expect(locked.locationName).toBe("Shibuya");
    expect(locked.sharedStaff).toBe(true);
  });

  it("reads an older server's 409 schedule_locked without location or shared staff", async () => {
    respondJson(409, { detail: { code: "schedule_locked", locked_by: "Ada", expires_at: "2026-11-09T10:00:00Z" } });
    const err = (await caught(generateScheduleStream(body, signal()))) as ScheduleLockedError;
    expect(err).toBeInstanceOf(ScheduleLockedError);
    expect(err.locationName).toBeNull();
    expect(err.sharedStaff).toBe(false);
  });

  it("leaves a 409 with another code as an ApiError", async () => {
    respondJson(409, { detail: { code: "other", message: "Busy" } });
    const err = await caught(generateScheduleStream(body, signal()));
    expect(err).toBeInstanceOf(ApiError);
    expect(err).not.toBeInstanceOf(ScheduleLockedError);
    expect((err as ApiError).status).toBe(409);
    expect((err as ApiError).message).toBe("Busy");
  });

  it("leaves a 409 with a non-JSON body as an ApiError with statusText", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>", { status: 409, statusText: "Conflict" }));
    const err = await caught(generateScheduleStream(body, signal()));
    expect(err).toBeInstanceOf(ApiError);
    expect(err).not.toBeInstanceOf(ScheduleLockedError);
    expect((err as ApiError).message).toBe("Conflict");
  });

  it("carries a 402 detail's message and code", async () => {
    respondJson(402, { detail: { code: "ai_credits_required", message: "Buy credits" } });
    const err = (await caught(generateScheduleStream(body, signal()))) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(402);
    expect(err.message).toBe("Buy credits");
    expect((err.data as { code?: string }).code).toBe("ai_credits_required");
  });

  it("uses a string detail as the message", async () => {
    respondJson(402, { detail: "Schedule quota exhausted." });
    const err = (await caught(generateScheduleStream(body, signal()))) as ApiError;
    expect(err.status).toBe(402);
    expect(err.message).toBe("Schedule quota exhausted.");
  });

  it("uses statusText for a list detail and keeps the list as data", async () => {
    respondJson(422, { detail: [{ msg: "bad week" }] }, "Unprocessable Entity");
    const err = (await caught(generateScheduleStream(body, signal()))) as ApiError;
    expect(err.status).toBe(422);
    expect(err.message).toBe("Unprocessable Entity");
    expect(err.data).toEqual([{ msg: "bad week" }]);
  });

  it("uses statusText for a non-JSON 500", async () => {
    fetchMock.mockResolvedValueOnce(new Response("<html>", { status: 500, statusText: "Internal Server Error" }));
    const err = (await caught(generateScheduleStream(body, signal()))) as ApiError;
    expect(err).toBeInstanceOf(ApiError);
    expect(err.message).toBe("Internal Server Error");
  });

  it("rejects a 2xx with no body", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 200 }));
    await expect(generateScheduleStream(body, signal())).rejects.toThrow("No readable stream available");
  });

  it("passes an AbortError through", async () => {
    fetchMock.mockRejectedValueOnce(new DOMException("aborted", "AbortError"));
    const err = (await caught(generateScheduleStream(body, signal()))) as Error;
    expect(err.name).toBe("AbortError");
  });

  it("still maps approve's 409 schedule_locked and keeps other details as data", async () => {
    respondJson(409, { detail: { code: "schedule_locked", locked_by: "Bo", expires_at: "2026-11-09T10:00:00Z" } });
    const locked = await caught(approveSchedule("s1"));
    expect(locked).toBeInstanceOf(ScheduleLockedError);
    expect((locked as ScheduleLockedError).lockedBy).toBe("Bo");

    respondJson(400, { detail: { code: "x", message: "Nope" } });
    const other = (await caught(approveSchedule("s1"))) as ApiError;
    expect(other).toBeInstanceOf(ApiError);
    expect(other.message).toBe("Nope");
    expect(other.data).toEqual({ code: "x", message: "Nope" });
  });
});
