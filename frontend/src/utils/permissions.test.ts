import { describe, expect, it } from "vitest";
import {
  ADMIN_ONLY_PATHS,
  canAccessLocation,
  canEditEmployee,
  canVisitPath,
  isAdminOnlyPath,
  isRestricted,
  managerAccess,
  restrictedLocationNames,
} from "./permissions";

const manager = (manager_type: string | null, ids?: string[] | null) => ({
  user_role: "manager",
  manager_type,
  accessible_location_ids: ids,
});

describe("managerAccess", () => {
  it("gives nobody admin access without a manager user", () => {
    expect(managerAccess(null)).toEqual({ isAdmin: false, accessibleLocationIds: null });
    expect(
      managerAccess({ user_role: "employee", manager_type: null, accessible_location_ids: null }),
    ).toEqual({ isAdmin: false, accessibleLocationIds: null });
  });

  it("treats admin and a legacy NULL type as unrestricted admins", () => {
    for (const type of ["admin", null]) {
      const access = managerAccess(manager(type, null));
      expect(access).toEqual({ isAdmin: true, accessibleLocationIds: null });
      expect(isRestricted(access)).toBe(false);
      expect(canAccessLocation(access, "anything")).toBe(true);
    }
  });

  it("ignores a stray location list on an admin", () => {
    expect(managerAccess(manager("admin", ["a"])).accessibleLocationIds).toBeNull();
  });

  it("restricts a regular manager to their locations", () => {
    const access = managerAccess(manager("regular", ["a", "b"]));
    expect(access).toEqual({ isAdmin: false, accessibleLocationIds: ["a", "b"] });
    expect(isRestricted(access)).toBe(true);
    expect(canAccessLocation(access, "a")).toBe(true);
    expect(canAccessLocation(access, "c")).toBe(false);
  });

  it("keeps a regular manager with no locations restricted to nothing", () => {
    const access = managerAccess(manager("regular", []));
    expect(isRestricted(access)).toBe(true);
    expect(canAccessLocation(access, "a")).toBe(false);
  });
});

describe("admin-only paths", () => {
  it("covers the company-wide pages", () => {
    for (const p of [
      "/manager/company",
      "/manager/team",
      "/manager/regions",
      "/manager/roles",
      "/manager/role-equivalents",
      "/manager/payroll",
    ]) {
      expect(ADMIN_ONLY_PATHS).toContain(p);
      expect(isAdminOnlyPath(p)).toBe(true);
      expect(isAdminOnlyPath(`${p}/`)).toBe(true);
    }
  });

  it("leaves location-scoped pages open", () => {
    for (const p of [
      "/manager/dashboard",
      "/manager/locations",
      "/manager/employees",
      "/manager/schedule",
      "/manager/export-schedules",
      "/manager/data-privacy",
      "/manager/roles-extra",
    ]) {
      expect(isAdminOnlyPath(p)).toBe(false);
    }
  });

  it("lets admins visit everything and regular managers only open pages", () => {
    const admin = managerAccess(manager("admin"));
    const regular = managerAccess(manager("regular", ["a"]));
    expect(canVisitPath(admin, "/manager/payroll")).toBe(true);
    expect(canVisitPath(regular, "/manager/payroll")).toBe(false);
    expect(canVisitPath(regular, "/manager/schedule")).toBe(true);
  });
});

describe("restrictedLocationNames", () => {
  const locs = [
    { id: "a", name: "Downtown" },
    { id: "b", name: "Uptown" },
  ];
  it("names only accessible locations, in list order", () => {
    expect(restrictedLocationNames(managerAccess(manager("regular", ["b"])), locs)).toEqual([
      "Uptown",
    ]);
    expect(restrictedLocationNames(managerAccess(manager("admin")), locs)).toEqual([
      "Downtown",
      "Uptown",
    ]);
  });
});

describe("canEditEmployee", () => {
  const regular = managerAccess(manager("regular", ["a", "b"]));

  it("lets an admin edit anyone, even with no locations", () => {
    const admin = managerAccess(manager("admin"));
    expect(canEditEmployee(admin, ["z"])).toBe(true);
    expect(canEditEmployee(admin, [])).toBe(true);
    expect(canEditEmployee(admin, null)).toBe(true);
    expect(canEditEmployee(admin, undefined)).toBe(true);
  });

  it("lets a regular manager edit only when every location is in scope", () => {
    expect(canEditEmployee(regular, ["a"])).toBe(true);
    expect(canEditEmployee(regular, ["a", "b"])).toBe(true);
    expect(canEditEmployee(regular, ["a", "c"])).toBe(false);
    expect(canEditEmployee(regular, ["c"])).toBe(false);
  });

  it("keeps an employee with no locations admin-only", () => {
    expect(canEditEmployee(regular, [])).toBe(false);
    expect(canEditEmployee(regular, null)).toBe(false);
    expect(canEditEmployee(regular, undefined)).toBe(false);
  });

  it("refuses everything to a regular manager with no locations", () => {
    const none = managerAccess(manager("regular", []));
    expect(canEditEmployee(none, ["a"])).toBe(false);
  });
});
