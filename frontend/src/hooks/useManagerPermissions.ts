import { useMemo } from "react";
import { useAuth } from "./useAuth";
import {
  canAccessLocation,
  canVisitPath,
  isRestricted,
  type ManagerAccess,
} from "../utils/permissions";

export interface ManagerPermissions extends ManagerAccess {
  /** True for a regular manager limited to some locations. */
  restricted: boolean;
  canAccessLocation: (locationId: string) => boolean;
  canVisitPath: (pathname: string) => boolean;
}

/**
 * The signed-in manager's permissions, read from the auth context
 * (`/auth/me`), so no extra fetch and every page updates together when the
 * user is re-fetched.
 *
 * - Admin managers: every location and every page.
 * - Regular managers: their assigned locations; company-wide pages hidden.
 */
export function useManagerPermissions(): ManagerPermissions {
  const { isAdmin, accessibleLocationIds } = useAuth();
  return useMemo(() => {
    const access: ManagerAccess = { isAdmin, accessibleLocationIds };
    return {
      ...access,
      restricted: isRestricted(access),
      canAccessLocation: (id: string) => canAccessLocation(access, id),
      canVisitPath: (path: string) => canVisitPath(access, path),
    };
  }, [isAdmin, accessibleLocationIds]);
}
