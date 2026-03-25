import type { Role } from "../types";
import { apiFetch } from "./client";

export function listRoles(): Promise<Role[]> {
  return apiFetch<Role[]>("/roles/");
}

export function createRole(body: {
  name: string;
  description?: string | null;
}): Promise<Role> {
  return apiFetch<Role>("/roles/", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateRole(
  id: string,
  body: { name?: string; description?: string | null }
): Promise<Role> {
  return apiFetch<Role>(`/roles/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteRole(id: string): Promise<void> {
  return apiFetch<void>(`/roles/${id}`, { method: "DELETE" });
}
