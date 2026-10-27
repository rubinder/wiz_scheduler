/**
 * Regular (location-scoped) managers never see company-wide pages in the
 * sidebar, and the route guard redirects them if they follow a link (#149).
 * A source check for the guard because the project has no DOM test
 * environment.
 */
import { describe, expect, it } from "vitest";
import { ADMIN_ONLY_PATHS } from "../../utils/permissions";
import {
  employeeNav,
  isGroup,
  managerNav,
  visibleEmployeeNav,
  visibleManagerNav,
  type NavEntry,
} from "./navItems";

const paths = (entries: NavEntry[]) =>
  entries.flatMap((e) => (isGroup(e) ? e.children.map((c) => c.to) : [e.to]));

const APP_SOURCE = import.meta.glob("../../App.tsx", {
  query: "?raw",
  import: "default",
  eager: true,
}) as Record<string, string>;

describe("manager navigation", () => {
  it("shows an admin every page", () => {
    expect(paths(visibleManagerNav({ isAdmin: true }, true, true))).toEqual(paths(managerNav));
  });

  it("hides every admin-only page from a regular manager", () => {
    const shown = paths(visibleManagerNav({ isAdmin: false }, true));
    for (const p of ADMIN_ONLY_PATHS) expect(shown).not.toContain(p);
    expect(shown).toContain("/manager/locations");
    expect(shown).toContain("/manager/schedule");
    expect(shown).toContain("/manager/data-privacy");
  });

  it("drops a group left with no pages", () => {
    const groups = visibleManagerNav({ isAdmin: false }, true)
      .filter(isGroup)
      .map((g) => g.labelKey);
    expect(groups).not.toContain("groupRoles");
    expect(groups).toContain("groupOrganization");
  });

  it("still waits for employees before showing employee-dependent pages", () => {
    const shown = paths(visibleManagerNav({ isAdmin: true }, false));
    expect(shown).not.toContain("/manager/employee-availability");
    expect(shown).not.toContain("/manager/employee-association");
  });

  it("lists every admin-only page in the nav, so hiding it is meaningful", () => {
    const all = paths(managerNav);
    for (const p of ADMIN_ONLY_PATHS) expect(all).toContain(p);
  });

  it("shows preference reviews only while the company switch is on (#189)", () => {
    for (const isAdmin of [true, false]) {
      expect(paths(visibleManagerNav({ isAdmin }, true, false))).not.toContain(
        "/manager/preference-reviews",
      );
      expect(paths(visibleManagerNav({ isAdmin }, true, true))).toContain(
        "/manager/preference-reviews",
      );
    }
    expect(ADMIN_ONLY_PATHS).not.toContain("/manager/preference-reviews");
  });

  it("guards admin-only routes in App.tsx with the same list", () => {
    const source = Object.values(APP_SOURCE)[0];
    expect(source).toMatch(/!isAdmin && isAdminOnlyPath\(pathname\)/);
    expect(source).toMatch(/<Navigate to="\/manager\/dashboard" replace \/>/);
    for (const p of ADMIN_ONLY_PATHS) {
      expect(source).toContain(`path="${p.replace("/manager/", "")}"`);
    }
  });
});

describe("employee navigation", () => {
  it("shows My Preferences only while the company switch is on (#189)", () => {
    expect(paths(visibleEmployeeNav(false))).not.toContain("/employee/preferences");
    expect(paths(visibleEmployeeNav(true))).toEqual(paths(employeeNav));
    expect(paths(visibleEmployeeNav(true))).toContain("/employee/preferences");
    expect(paths(visibleEmployeeNav(false))).toContain("/employee/availability");
  });

  it("routes both preference pages in App.tsx", () => {
    const source = Object.values(APP_SOURCE)[0];
    expect(source).toContain('path="preferences"');
    expect(source).toContain('path="preference-reviews"');
  });
});
