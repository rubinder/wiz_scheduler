import type { TurnOutput } from "../api/publicReservations";

export type ChatMessage = { from: "diner" | "host"; text: string };
export type ChatAction = "confirm_hold" | "decline_hold" | "confirm_cancel" | "confirm_modify";

/** Append the diner's message (if any) and then the host's reply. */
export function reduceTurn(messages: ChatMessage[], sent: string | null, out: TurnOutput): ChatMessage[] {
  const next = [...messages];
  if (sent !== null) next.push({ from: "diner", text: sent });
  next.push({ from: "host", text: out.reply_text });
  return next;
}

/** Buttons offered for what the host expects next. */
export function actionsFor(expect: string): ChatAction[] {
  switch (expect) {
    case "confirm_hold": return ["confirm_hold", "decline_hold"];
    case "confirm_cancel": return ["confirm_cancel"];
    case "confirm_modify": return ["confirm_modify", "decline_hold"];
    default: return [];
  }
}

export const ACTION_LABELS: Record<ChatAction, string> = {
  confirm_hold: "Confirm booking",
  decline_hold: "Pick another time",
  confirm_cancel: "Yes, cancel it",
  confirm_modify: "Confirm change",
};

/** Placeholder and input mode for the field-hint expect values. */
export function inputHint(expect: string): { placeholder: string; inputMode: "text" | "tel" | "numeric" } {
  switch (expect) {
    case "phone_number": return { placeholder: "Your phone number", inputMode: "tel" };
    case "party_size": return { placeholder: "Number of guests", inputMode: "numeric" };
    case "confirmation_code": return { placeholder: "Confirmation code", inputMode: "text" };
    default: return { placeholder: "Type a message", inputMode: "text" };
  }
}
