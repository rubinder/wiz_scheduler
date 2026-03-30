import type { CondensedRole } from "../types";
import { apiFetch } from "./client";

export function listCondensedRoles(): Promise<CondensedRole[]> {
  return apiFetch<CondensedRole[]>("/condensed-roles/");
}

export function createCondensedRole(body: {
  name: string;
  role_ids: string[];
}): Promise<CondensedRole> {
  return apiFetch<CondensedRole>("/condensed-roles/", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateCondensedRole(
  id: string,
  body: { name?: string; role_ids?: string[] }
): Promise<CondensedRole> {
  return apiFetch<CondensedRole>(`/condensed-roles/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteCondensedRole(id: string): Promise<void> {
  return apiFetch<void>(`/condensed-roles/${id}`, { method: "DELETE" });
}
