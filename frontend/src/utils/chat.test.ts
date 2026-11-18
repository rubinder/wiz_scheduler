import { describe, expect, it } from "vitest";
import type { TurnOutput } from "../api/publicReservations";
import { actionLabel, actionsFor, inputHint, noticeForError, proposalText, reduceTurn, sanitizeTel, startErrorFor } from "./chat";

const out = (reply_text: string): TurnOutput => ({ reply_text, expect: "text", hold: null, ended: false, handoff: null });

describe("reduceTurn", () => {
  it("appends diner then host", () => {
    expect(reduceTurn([], "hi", out("hello"))).toEqual([
      { from: "diner", text: "hi" },
      { from: "host", text: "hello" },
    ]);
  });
  it("null sent appends host only and does not mutate", () => {
    const before = [{ from: "host" as const, text: "a" }];
    expect(reduceTurn(before, null, out("b"))).toEqual([...before, { from: "host", text: "b" }]);
    expect(before).toHaveLength(1);
  });
});

describe("actionsFor", () => {
  it("maps expect values", () => {
    expect(actionsFor("confirm_hold")).toEqual(["confirm_hold", "decline_hold"]);
    expect(actionsFor("confirm_cancel")).toEqual(["confirm_cancel", "decline_hold"]);
    expect(actionsFor("confirm_modify")).toEqual(["confirm_modify", "decline_hold"]);
    for (const e of ["text", "handoff", "phone_number", "party_size", "confirmation_code"] as const) expect(actionsFor(e)).toEqual([]);
  });
});

describe("actionLabel", () => {
  it("labels the way out by what is being confirmed", () => {
    expect(actionLabel("decline_hold", "confirm_hold")).toBe("Pick another time");
    expect(actionLabel("decline_hold", "confirm_cancel")).toBe("Keep my booking");
    expect(actionLabel("decline_hold", "confirm_modify")).toBe("Keep my booking");
    expect(actionLabel("confirm_cancel", "confirm_cancel")).toBe("Yes, cancel it");
  });
});

describe("proposalText", () => {
  const current = { local_date: "2026-11-20", local_time: "19:00", party_size: 2 };
  it("shows a change as current → proposed", () => {
    expect(proposalText({ kind: "modify", current, proposed: { local_date: "2026-11-20", local_time: "20:00", party_size: 3 } })).toBe(
      "Change: 2026-11-20 at 19:00, party of 2 → 2026-11-20 at 20:00, party of 3",
    );
  });
  it("shows a cancel as the booking being cancelled", () => {
    expect(proposalText({ kind: "cancel", current, proposed: null })).toBe("Cancel: 2026-11-20 at 19:00, party of 2");
  });
});

describe("inputHint", () => {
  it("hints field types", () => {
    expect(inputHint("phone_number").inputMode).toBe("tel");
    expect(inputHint("party_size").inputMode).toBe("numeric");
    expect(inputHint("text").placeholder).toBe("Type a message");
  });
});

describe("noticeForError", () => {
  it("maps statuses", () => {
    expect(noticeForError(409, { code: "turn_in_progress" })).toEqual({ notice: "Still working on your last message…", ended: false });
    expect(noticeForError(429).notice).toBe("Too many messages, try again later");
    expect(noticeForError(404)).toEqual({ notice: "This chat has ended", ended: true });
    expect(noticeForError(409, { code: "other" }).ended).toBe(false);
    expect(noticeForError(0).ended).toBe(false);
  });
});

describe("startErrorFor", () => {
  it("distinguishes 404, 429 and others", () => {
    expect(startErrorFor(404)).toEqual({ message: "This booking page is not available.", retry: false });
    expect(startErrorFor(429)).toEqual({ message: "Too many requests, try again in a minute", retry: true });
    expect(startErrorFor(500)).toEqual({ message: "Couldn't start the chat", retry: true });
  });
});

describe("sanitizeTel", () => {
  it("keeps only + and digits", () => {
    expect(sanitizeTel("+1 (212) 555-0100")).toBe("+12125550100");
    expect(sanitizeTel('x"><script>')).toBe("");
  });
});
