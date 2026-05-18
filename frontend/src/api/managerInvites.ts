import { apiFetch } from "./client";
import type { ManagerInvite, ManagerInviteInfo } from "../types";

export async function listManagerInvites(): Promise<ManagerInvite[]> {
  return apiFetch<ManagerInvite[]>("/manager-invites/");
}

export async function createManagerInvite(email: string): Promise<ManagerInvite & { token: string; invite_url: string }> {
  return apiFetch("/manager-invites/", {
    method: "POST",
    body: JSON.stringify({ email }),
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
