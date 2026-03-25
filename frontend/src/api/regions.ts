import type { Region } from "../types";
import { apiFetch } from "./client";

export function listRegions(): Promise<Region[]> {
  return apiFetch<Region[]>("/regions/");
}

export function createRegion(body: {
  name: string;
  geo_bounds?: Record<string, unknown> | null;
}): Promise<Region> {
  return apiFetch<Region>("/regions/", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateRegion(
  id: string,
  body: { name?: string; geo_bounds?: Record<string, unknown> | null }
): Promise<Region> {
  return apiFetch<Region>(`/regions/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteRegion(id: string): Promise<void> {
  return apiFetch<void>(`/regions/${id}`, { method: "DELETE" });
}
