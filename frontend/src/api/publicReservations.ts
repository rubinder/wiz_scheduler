import { ApiError } from "./client";

// Signed-out diner endpoints: deliberately no Authorization header and no
// session handling, unlike apiFetch.
const BASE = "/api/v1/public/reservations";

export type Expect =
  | "text" | "confirm_hold" | "confirm_cancel" | "confirm_modify"
  | "phone_number" | "party_size" | "confirmation_code" | "handoff";
export interface LocationInfo { location_name: string; timezone: string; greeting: string }
export interface Hold {
  party_size: number;
  local_date: string;
  local_time: string;
  start_iso: string;
  status: string;
  held_until?: string | null;
  confirmation_code?: string;
}
/** A booking's local date ("YYYY-MM-DD"), local time ("HH:MM") and party size. */
export interface SlotSummary { local_date: string; local_time: string; party_size: number }
/** What a confirm_modify / confirm_cancel button applies (built by the server, not the model). */
export interface Proposal {
  kind: "modify" | "cancel";
  current: SlotSummary;
  proposed: SlotSummary | null;
}
export interface TurnOutput {
  reply_text: string;
  expect: Expect;
  hold: Hold | null;
  ended: boolean;
  handoff: { reason: string; phone: string | null } | null;
  proposal?: Proposal | null;
}
export interface Conversation { conversation_token: string; reply_text: string; expect: Expect }
export type TurnInput = { text: string } | { action: "confirm_hold" | "decline_hold" | "confirm_cancel" | "confirm_modify" };

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: init?.body ? { "Content-Type": "application/json" } : undefined,
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    const detail = body?.detail;
    throw new ApiError(res.status, typeof detail === "string" ? detail : (detail?.message || res.statusText), detail);
  }
  return res.json() as Promise<T>;
}

const where = (slug: string, locationId: string) => `/${encodeURIComponent(slug)}/${encodeURIComponent(locationId)}`;

export const getLocationInfo = (slug: string, locationId: string) =>
  call<LocationInfo>(where(slug, locationId));

export const startConversation = (slug: string, locationId: string) =>
  call<Conversation>(`${where(slug, locationId)}/conversations`, { method: "POST" });

export const sendTurn = (slug: string, locationId: string, token: string, input: TurnInput) =>
  call<TurnOutput>(`${where(slug, locationId)}/conversations/${encodeURIComponent(token)}/turns`, {
    method: "POST",
    body: JSON.stringify(input),
  });
