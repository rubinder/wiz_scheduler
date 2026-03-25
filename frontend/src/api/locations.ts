import type { Location } from "../types";
import { apiFetch } from "./client";

export function listLocations(): Promise<Location[]> {
  return apiFetch<Location[]>("/locations/");
}

export function createLocation(body: {
  region_id: string;
  name: string;
  address?: string | null;
  geo_coord?: Record<string, unknown> | null;
  timezone: string;
}): Promise<Location> {
  return apiFetch<Location>("/locations/", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function updateLocation(
  id: string,
  body: {
    region_id?: string;
    name?: string;
    address?: string | null;
    geo_coord?: Record<string, unknown> | null;
    timezone?: string;
  }
): Promise<Location> {
  return apiFetch<Location>(`/locations/${id}`, {
    method: "PUT",
    body: JSON.stringify(body),
  });
}

export function deleteLocation(id: string): Promise<void> {
  return apiFetch<void>(`/locations/${id}`, { method: "DELETE" });
}
