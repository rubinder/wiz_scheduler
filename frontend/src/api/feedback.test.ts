/**
 * submitFeedback (#237): the feedback form's request goes through apiFetch.
 * The project has no DOM test environment, so the request is checked against
 * a stubbed fetch. This also guards the old bug where the form read the
 * token from localStorage under "access_token" instead of "token".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, errorMessage } from "./client";
import { submitFeedback } from "./feedback";

describe("submitFeedback", () => {
  const fetchMock = vi.fn();
  let store: Record<string, string>;
  let getItem: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    store = { token: "tok" };
    getItem = vi.fn((k: string) => store[k] ?? null);
    vi.stubGlobal("localStorage", {
      getItem,
      setItem: (k: string, v: string) => (store[k] = v),
      removeItem: (k: string) => delete store[k],
    });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    fetchMock.mockReset();
  });

  const respond = (status: number, body: unknown) =>
    fetchMock.mockResolvedValueOnce(
      new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json" },
      }),
    );

  const body = {
    subject: "Broken button",
    message: "The save button does nothing.",
    type: "bug",
  } as const;
  const QUOTA_MSG =
    "Free plan limited to 5 feedback submissions per month. Upgrade to Pro for unlimited submissions.";
  const RATE_MSG = "Too many feedback submissions. Try again in a few minutes.";

  it("POSTs the JSON body to /api/v1/feedback/submit", async () => {
    respond(200, { success: true, message: "Thank you for your feedback! We'll review it shortly." });
    await submitFeedback(body);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/feedback/submit");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({
      subject: body.subject,
      message: body.message,
      type: "bug",
    });
    expect(init.headers["Content-Type"]).toBe("application/json");
  });

  it("sends the token stored under 'token', never 'access_token'", async () => {
    respond(200, { success: true, message: "ok" });
    await submitFeedback(body);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer tok");
    expect(getItem).not.toHaveBeenCalledWith("access_token");
  });

  it("resolves with the body on success:true", async () => {
    const ok = { success: true, message: "Thank you for your feedback! We'll review it shortly." };
    respond(200, ok);
    await expect(submitFeedback(body)).resolves.toEqual(ok);
  });

  it("rejects with the backend message on 200 success:false", async () => {
    respond(200, { success: false, message: "Email service not configured" });
    const err = await submitFeedback(body).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(Error);
    expect((err as Error).message).toBe("Email service not configured");
    expect(errorMessage(err, "fallback")).toBe("Email service not configured");
  });

  it("falls back to the default message when success:false carries no message", async () => {
    respond(200, { success: false, message: "" });
    const err = await submitFeedback(body).catch((e: unknown) => e);
    expect((err as Error).message).toBe("Failed to submit feedback");
  });

  it("surfaces the quota detail.message for the free-plan 429", async () => {
    respond(429, {
      detail: {
        code: "feedback_quota_exceeded",
        message: QUOTA_MSG,
        limit: 5,
        current_month_count: 5,
        resets_at: "2026-12-01T00:00:00+00:00",
      },
    });
    const err = await submitFeedback(body).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(429);
    expect(errorMessage(err, "fallback")).toBe(QUOTA_MSG);
  });

  it("surfaces the rate-limit string for the IP 429", async () => {
    respond(429, { detail: RATE_MSG });
    const err = await submitFeedback(body).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(429);
    expect((err as ApiError).message).toBe(RATE_MSG);
    expect(errorMessage(err, "fallback")).toBe(RATE_MSG);
  });

  it('omits Authorization and surfaces "Not authenticated" when no token is stored', async () => {
    delete store.token;
    respond(403, { detail: "Not authenticated" });
    const err = await submitFeedback(body).catch((e: unknown) => e);
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBeUndefined();
    expect(errorMessage(err, "fallback")).toBe("Not authenticated");
  });

  it("shows the first pydantic msg for a 422", async () => {
    respond(422, {
      detail: [
        {
          loc: ["body", "subject"],
          msg: "String should have at least 5 characters",
          type: "string_too_short",
        },
      ],
    });
    const err = await submitFeedback(body).catch((e: unknown) => e);
    expect(errorMessage(err, "fallback")).toBe("String should have at least 5 characters");
  });

  it("rejects with statusText for a non-JSON error body", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("<html>bad gateway</html>", { status: 502, statusText: "Bad Gateway" }),
    );
    const err = await submitFeedback(body).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(errorMessage(err, "fallback")).toBe("Bad Gateway");
  });

  it("propagates a network failure as an Error", async () => {
    fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));
    const err = await submitFeedback(body).catch((e: unknown) => e);
    expect(errorMessage(err, "fallback")).toBe("Failed to fetch");
  });
});
