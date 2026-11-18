import type { Conversation, Expect, Proposal, SlotSummary, TurnOutput } from "../api/publicReservations";

export type ChatMessage = { from: "diner" | "host"; text: string };
export type ChatAction = "confirm_hold" | "decline_hold" | "confirm_cancel" | "confirm_modify";

/** Append the diner's message (if any) and then the host's reply. */
export function reduceTurn(messages: ChatMessage[], sent: string | null, out: TurnOutput): ChatMessage[] {
  const next = [...messages];
  if (sent !== null) next.push({ from: "diner", text: sent });
  next.push({ from: "host", text: out.reply_text });
  return next;
}

/** Buttons offered for what the host expects next. Every confirm has a way out. */
export function actionsFor(expect: Expect): ChatAction[] {
  switch (expect) {
    case "confirm_hold": return ["confirm_hold", "decline_hold"];
    case "confirm_cancel": return ["confirm_cancel", "decline_hold"];
    case "confirm_modify": return ["confirm_modify", "decline_hold"];
    default: return [];
  }
}

/** A button's label. "No" means letting a hold go, or keeping a booking unchanged. */
export function actionLabel(action: ChatAction, expect: Expect): string {
  switch (action) {
    case "confirm_hold": return "Confirm booking";
    case "confirm_cancel": return "Yes, cancel it";
    case "confirm_modify": return "Confirm change";
    case "decline_hold":
      return expect === "confirm_cancel" || expect === "confirm_modify" ? "Keep my booking" : "Pick another time";
  }
}

const slotText = (s: SlotSummary): string => `${s.local_date} at ${s.local_time}, party of ${s.party_size}`;

/** The structured summary shown above a change or cancel button (date strings are shown as-is, never parsed). */
export function proposalText(p: Proposal): string {
  if (p.kind === "cancel" || !p.proposed) return `Cancel: ${slotText(p.current)}`;
  return `Change: ${slotText(p.current)} → ${slotText(p.proposed)}`;
}

/** Placeholder and input mode for the field-hint expect values. */
export function inputHint(expect: Expect): { placeholder: string; inputMode: "text" | "tel" | "numeric" } {
  switch (expect) {
    case "phone_number": return { placeholder: "Your phone number", inputMode: "tel" };
    case "party_size": return { placeholder: "Number of guests", inputMode: "numeric" };
    case "confirmation_code": return { placeholder: "Confirmation code", inputMode: "text" };
    default: return { placeholder: "Type a message", inputMode: "text" };
  }
}

/** Whether a chat is already over when it starts (the location is at its spend cap). */
export function startEnded(c: Pick<Conversation, "ended">): boolean {
  return c.ended === true;
}

/** What to tell the diner when a turn request fails, and whether the chat is over. */
export function noticeForError(status: number, detail?: { code?: string } | null): { notice: string; ended: boolean } {
  if (status === 409 && detail?.code === "turn_in_progress") return { notice: "Still working on your last message…", ended: false };
  if (status === 409 && detail?.code === "location_busy") return { notice: "Lots of people are booking right now — please try again in a minute.", ended: false };
  if (status === 429) return { notice: "Too many messages, try again later", ended: false };
  if (status === 404) return { notice: "This chat has ended", ended: true };
  return { notice: "Something went wrong. Please try again.", ended: false };
}

/** Message (and whether Retry makes sense) when the chat fails to start. */
export function startErrorFor(status: number): { message: string; retry: boolean } {
  if (status === 404) return { message: "This booking page is not available.", retry: false };
  if (status === 429) return { message: "Too many requests, try again in a minute", retry: true };
  return { message: "Couldn't start the chat", retry: true };
}

/** Digits and a leading-safe "+" only, for a tel: href; "" if nothing usable. */
export function sanitizeTel(phone: string): string {
  return phone.replace(/[^+0-9]/g, "");
}
