import { apiFetch } from "./client";
import type { ManagerInvite, ManagerInviteInfo, ManagerType } from "../types";

export async function listManagerInvites(): Promise<ManagerInvite[]> {
  return apiFetch<ManagerInvite[]>("/manager-invites/");
}

/**
 * Invite a manager. A regular (location-scoped) invite needs at least one
 * location; an admin invite sends none.
 */
export async function createManagerInvite(
  email: string,
  managerType: ManagerType = "admin",
  locationIds: string[] = []
): Promise<ManagerInvite & { token: string; invite_url: string }> {
  return apiFetch("/manager-invites/", {
    method: "POST",
    body: JSON.stringify(
      managerType === "regular"
        ? { email, manager_type: managerType, location_ids: locationIds }
        : { email, manager_type: managerType }
    ),
  });
}

export async function getManagerInviteInfo(token: string): Promise<ManagerInviteInfo> {
  return apiFetch<ManagerInviteInfo>(`/manager-invites/info?token=${encodeURIComponent(token)}`);
}

export async function acceptManagerInvite(args: {
  token: string;
  company_id: string;
  full_name: string;
  password: string;
}): Promise<{ access_token: string; token_type: string }> {
  return apiFetch("/manager-invites/accept", {
    method: "POST",
    body: JSON.stringify(args),
  });
}
