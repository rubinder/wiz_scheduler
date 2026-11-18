import { apiFetch } from "./client";
import type {
  BookingIn,
  ConversationDetailOut,
  ConversationOut,
  DinerDataOut,
  DinerEraseOut,
  FloorIn,
  FloorOut,
  ReservationOut,
  ReservationPatch,
  ReservationsConfig,
  RulesOut,
  RulesSchema,
  SettableStatus,
  SlotOut,
  WalkInIn,
} from "../types/reservations";

const base = (locationId: string) => `/reservations/locations/${locationId}`;

export function getConfig(): Promise<ReservationsConfig> {
  return apiFetch("/reservations/config");
}

export function getFloor(locationId: string): Promise<FloorOut> {
  return apiFetch(`${base(locationId)}/floor`);
}

export function saveFloor(locationId: string, floor: FloorIn): Promise<FloorOut> {
  return apiFetch(`${base(locationId)}/floor`, { method: "PUT", body: JSON.stringify(floor) });
}

export function getRules(locationId: string): Promise<RulesOut> {
  return apiFetch(`${base(locationId)}/rules`);
}

export function saveRules(locationId: string, rules: RulesSchema): Promise<RulesOut> {
  return apiFetch(`${base(locationId)}/rules`, { method: "PUT", body: JSON.stringify(rules) });
}

/** Staff view by default; applyCutoff previews what diners see. */
export function getSlots(
  locationId: string,
  partySize: number,
  date: string,
  applyCutoff?: boolean,
): Promise<SlotOut[]> {
  const qs = new URLSearchParams({ party_size: String(partySize), date });
  if (applyCutoff) qs.set("apply_cutoff", "true");
  return apiFetch(`${base(locationId)}/slots?${qs.toString()}`);
}

export function listReservations(locationId: string, date: string): Promise<ReservationOut[]> {
  return apiFetch(`${base(locationId)}/reservations?${new URLSearchParams({ date }).toString()}`);
}

/** Every live approval request at the location, on any day, soonest first. */
export function listApprovals(locationId: string): Promise<ReservationOut[]> {
  return apiFetch(`${base(locationId)}/approvals`);
}

export function createReservation(locationId: string, body: BookingIn): Promise<ReservationOut> {
  return apiFetch(`${base(locationId)}/reservations`, { method: "POST", body: JSON.stringify(body) });
}

export function seatWalkIn(locationId: string, body: WalkInIn): Promise<ReservationOut> {
  return apiFetch(`${base(locationId)}/walk-ins`, { method: "POST", body: JSON.stringify(body) });
}

export function updateReservation(id: string, body: ReservationPatch): Promise<ReservationOut> {
  return apiFetch(`/reservations/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export function setReservationStatus(id: string, status: SettableStatus): Promise<ReservationOut> {
  return apiFetch(`/reservations/${id}/status`, { method: "POST", body: JSON.stringify({ status }) });
}

export function approveReservation(id: string): Promise<ReservationOut> {
  return apiFetch(`/reservations/${id}/approve`, { method: "POST" });
}

export function declineReservation(id: string): Promise<ReservationOut> {
  return apiFetch(`/reservations/${id}/decline`, { method: "POST" });
}

/** The AI host's conversations started on `date` (the location's local day), newest first. */
export function listConversations(
  locationId: string,
  date: string,
  outcome?: string,
): Promise<ConversationOut[]> {
  const qs = new URLSearchParams({ date });
  if (outcome) qs.set("outcome", outcome);
  return apiFetch(`${base(locationId)}/conversations?${qs.toString()}`);
}

export function getConversation(id: string): Promise<ConversationDetailOut> {
  return apiFetch(`/reservations/conversations/${id}`);
}

/** One diner's reservations and conversations at a location, by E.164 phone (an access request). */
export function exportDinerData(locationId: string, phone: string): Promise<DinerDataOut> {
  return apiFetch(`${base(locationId)}/diner-data`, { method: "POST", body: JSON.stringify({ phone }) });
}

/** Erase one diner's data at a location: contact details scrubbed, conversations deleted. */
export function eraseDinerData(locationId: string, phone: string): Promise<DinerEraseOut> {
  return apiFetch(`${base(locationId)}/diner-data/erase`, { method: "POST", body: JSON.stringify({ phone }) });
}
