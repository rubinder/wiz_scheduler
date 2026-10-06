import { useEffect, useState } from "react";
import { useAuth } from "./useAuth";
import * as managersApi from "../api/managers";

interface ManagerPermissions {
  isAdmin: boolean;
  accessibleLocationIds: Set<string>;
  isLoading: boolean;
  error: string | null;
}

/**
 * Hook for checking manager location permissions.
 *
 * - Admin managers: can access all locations
 * - Regular managers: can only access assigned locations
 * - Non-managers: no location access
 */
export function useManagerPermissions(): ManagerPermissions {
  const { user } = useAuth();
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accessibleLocationIds, setAccessibleLocationIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    const loadPermissions = async () => {
      try {
        setIsLoading(true);
        setError(null);

        // Non-managers have no location access
        if (!user || user.user_role !== "manager") {
          setAccessibleLocationIds(new Set());
          setIsLoading(false);
          return;
        }

        // Fetch accessible locations from API
        const locations = await managersApi.getAccessibleLocations();
        const ids = new Set(locations.map((loc) => loc.id));
        setAccessibleLocationIds(ids);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to load permissions");
      } finally {
        setIsLoading(false);
      }
    };

    loadPermissions();
  }, [user]);

  return {
    isAdmin: user?.manager_type === "admin" || false,
    accessibleLocationIds,
    isLoading,
    error,
  };
}

/**
 * Check if a manager can access a specific location.
 */
export function canAccessLocation(
  permissions: ManagerPermissions,
  locationId: string
): boolean {
  if (permissions.isAdmin) {
    return true;
  }
  return permissions.accessibleLocationIds.has(locationId);
}
