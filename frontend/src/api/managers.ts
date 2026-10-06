import type { Location } from "../types";
import { apiFetch } from "./client";

export interface ManagerLocationAssignment {
  id: string;
  location_id: string;
  location: Location;
}

/**
 * Get all locations accessible to the current manager.
 */
export function getAccessibleLocations(): Promise<Location[]> {
  return apiFetch<Location[]>("/managers/me/locations");
}

/**
 * Get all locations assigned to a specific manager.
 */
export function getManagerLocations(managerId: string): Promise<Location[]> {
  return apiFetch<Location[]>(`/managers/${managerId}/locations`);
}

/**
 * Assign a location to a regular manager (admin only).
 */
export function assignLocationToManager(
  managerId: string,
  locationId: string
): Promise<Location> {
  return apiFetch<Location>(`/managers/${managerId}/locations/${locationId}`, {
    method: "POST",
  });
}

/**
 * Revoke a location from a regular manager (admin only).
 */
export function revokeLocationFromManager(
  managerId: string,
  locationId: string
): Promise<void> {
  return apiFetch<void>(`/managers/${managerId}/locations/${locationId}`, {
    method: "DELETE",
  });
}
