import type { BulkUploadResponse, Role } from "../types";
import { apiFetch } from "./client";

export function listRoles(includeArchived = false): Promise<Role[]> {
  return apiFetch<Role[]>(
    includeArchived ? "/roles/?include_archived=true" : "/roles/"
  );
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

export function restoreRole(id: string): Promise<Role> {
  return apiFetch<Role>(`/roles/${id}/restore`, { method: "POST" });
}

export async function bulkUploadRoles(
  file: File
): Promise<BulkUploadResponse> {
  const formData = new FormData();
  formData.append("file", file);
  return apiFetch<BulkUploadResponse>("/roles/bulk-upload", {
    method: "POST",
    body: formData as unknown as BodyInit,
  });
}
