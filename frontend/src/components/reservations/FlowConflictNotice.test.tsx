import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import en from "../../i18n/en";
import { isFlowConflict } from "../../utils/conversationFlow";
import FlowConflictNotice from "./FlowConflictNotice";

const f = en.reservations.flow;

describe("isFlowConflict", () => {
  it("is the server's 409 flow_conflict and nothing else", () => {
    expect(isFlowConflict(409, { code: "flow_conflict", message: "x" })).toBe(true);
    expect(isFlowConflict(409, { code: "draft_in_progress" })).toBe(false);
    expect(isFlowConflict(422, { code: "flow_conflict" })).toBe(false);
    expect(isFlowConflict(409, null)).toBe(false);
    expect(isFlowConflict(409, "flow_conflict")).toBe(false);
  });
});

describe("FlowConflictNotice", () => {
  it("says someone else saved and offers Reload", () => {
    const html = renderToStaticMarkup(<FlowConflictNotice message={f.conflict} reloadLabel={f.reload} onReload={() => {}} />);
    expect(html).toContain(f.conflict);
    expect(html).toMatch(/<button[^>]*type="button"[^>]*>Reload<\/button>/);
    expect(html).toContain('role="alert"');
  });
});
