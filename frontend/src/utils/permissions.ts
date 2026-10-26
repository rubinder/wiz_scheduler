/**
 * Location-scoped managers (#149): what the signed-in manager may see.
 *
 * Everything here derives from `/auth/me` (`manager_type`,
 * `accessible_location_ids`), so the sidebar, the route guard and every
 * page read one answer. The API is the security boundary; this only hides
 * what a regular manager would be refused anyway.
 */
import type { User } from "../types";

export interface ManagerAccess {
  /** Full company access. Only `manager_type === "regular"` is restricted. */
  isAdmin: boolean;
  /** Location ids a regular manager may use; null means every location. */
  accessibleLocationIds: string[] | null;
}

type AccessUser = Pick<User, "user_role" | "manager_type"> & {
  accessible_location_ids?: string[] | null;
};

export function managerAccess(user: AccessUser | null | undefined): ManagerAccess {
  if (!user || user.user_role !== "manager") {
    return { isAdmin: false, accessibleLocationIds: null };
  }
  // A legacy NULL manager_type means admin, as on the server.
  if (user.manager_type !== "regular") {
    return { isAdmin: true, accessibleLocationIds: null };
  }
  return {
    isAdmin: false,
    accessibleLocationIds: user.accessible_location_ids ?? null,
  };
}

/** True when the manager is limited to a subset of locations. */
export function isRestricted(access: ManagerAccess): boolean {
  return !access.isAdmin && access.accessibleLocationIds !== null;
}

export function canAccessLocation(access: ManagerAccess, locationId: string): boolean {
  if (access.accessibleLocationIds === null) return true;
  return access.accessibleLocationIds.includes(locationId);
}

/**
 * Company-wide pages (spec Q1): company settings, regions, roles, role
 * equivalents, the team, and payroll. Hidden from regular managers'
 * navigation and redirected to the dashboard by the route guard.
 */
export const ADMIN_ONLY_PATHS: readonly string[] = [
  "/manager/company",
  "/manager/team",
  "/manager/regions",
  "/manager/roles",
  "/manager/role-equivalents",
  "/manager/payroll",
];

export function isAdminOnlyPath(pathname: string): boolean {
  const path = pathname.replace(/\/+$/, "");
  return ADMIN_ONLY_PATHS.some((p) => path === p || path.startsWith(`${p}/`));
}

export function canVisitPath(access: ManagerAccess, pathname: string): boolean {
  return access.isAdmin || !isAdminOnlyPath(pathname);
}

/**
 * Names for the "Restricted to" badge, in the order the locations list
 * gives them. `locations` comes from GET /locations, which the API already
 * filters to the manager's scope; the id check keeps the badge honest if a
 * caller passes an unfiltered list.
 */
export function restrictedLocationNames(
  access: ManagerAccess,
  locations: { id: string; name: string }[],
): string[] {
  return locations
    .filter((l) => canAccessLocation(access, l.id))
    .map((l) => l.name);
}
