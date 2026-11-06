/**
 * The feedback form must submit through the API layer (#237).
 *
 * It used to call fetch() itself and read the token from
 * localStorage["access_token"], a key nothing writes, so every submission went
 * out as "Bearer null". This pins the fix: the form calls submitFeedback, which
 * goes through apiFetch, and never reads the token on its own.
 *
 * A source check because the project has no DOM test environment.
 */
import { describe, expect, it } from "vitest";

const SOURCE = (
  import.meta.glob("./FeedbackForm.tsx", { query: "?raw", import: "default", eager: true }) as Record<string, string>
)["./FeedbackForm.tsx"];

describe("FeedbackForm goes through the API layer", () => {
  it("loads the form source", () => {
    expect(SOURCE).toBeTypeOf("string");
  });

  it("imports submitFeedback from ../api/feedback", () => {
    expect(SOURCE).toMatch(/import\s*\{[^}]*\bsubmitFeedback\b[^}]*\}\s*from\s*["']\.\.\/api\/feedback["']/);
  });

  it("reports errors through errorMessage from ../api/client", () => {
    expect(SOURCE).toMatch(/import\s*\{[^}]*\berrorMessage\b[^}]*\}\s*from\s*["']\.\.\/api\/client["']/);
    expect(SOURCE).toMatch(/errorMessage\(\s*err\s*,\s*["']Failed to submit feedback["']\s*\)/);
  });

  it("never reads the token itself", () => {
    expect(SOURCE).not.toMatch(/localStorage/);
    expect(SOURCE).not.toMatch(/access_token/);
  });

  it("uses FeedbackType instead of repeating the literal union", () => {
    expect(SOURCE).not.toMatch(/"bug"\s*\|\s*"feature"/);
  });
});
