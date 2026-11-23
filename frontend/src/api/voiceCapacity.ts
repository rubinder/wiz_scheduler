import { apiFetch } from "./client";
import type { CapacityRequestBody } from "../utils/voiceCapacity";

/** The AI host billing page's "Call capacity" card (decision 103). */
export interface VoiceCapacity {
  voice_locations: { location_id: string; name: string }[];
  /** Simultaneous calls the company may hold. */
  allocation: number;
  /** The busiest moment in the last 30 days, or null without calls. */
  busiest: { peak_concurrent: number; day: string } | null;
  pending_request: { expected_peak: number; created_at: string } | null;
  default_staff_transfer_number: string | null;
  max_expected_peak: number;
}

const BASE = "/reservations/voice-capacity";

export function getVoiceCapacity(): Promise<VoiceCapacity> {
  return apiFetch(BASE);
}

export function requestVoiceCapacity(body: CapacityRequestBody): Promise<VoiceCapacity> {
  return apiFetch(`${BASE}/requests`, { method: "POST", body: JSON.stringify(body) });
}

export function saveDefaultTransferNumber(body: {
  default_staff_transfer_number: string | null;
}): Promise<VoiceCapacity> {
  return apiFetch(`${BASE}/transfer-number`, { method: "PUT", body: JSON.stringify(body) });
}
