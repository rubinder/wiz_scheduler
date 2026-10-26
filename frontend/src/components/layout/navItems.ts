/**
 * Sidebar navigation definitions, kept apart from the component so the
 * visibility rules can be tested without a DOM.
 */
import { isAdminOnlyPath, type ManagerAccess } from "../../utils/permissions";

export interface NavItem {
  to: string;
  labelKey: string;
  /** Hidden until the company has at least one employee. */
  requiresEmployees?: boolean;
}

export interface NavGroup {
  labelKey: string;
  children: NavItem[];
}

export type NavEntry = NavItem | NavGroup;

export function isGroup(entry: NavEntry): entry is NavGroup {
  return (entry as NavGroup).children !== undefined;
}

export const managerNav: NavEntry[] = [
  { to: "/manager/dashboard", labelKey: "dashboard" },
  {
    labelKey: "groupOrganization",
    children: [
      { to: "/manager/company", labelKey: "company" },
      { to: "/manager/team", labelKey: "team" },
      { to: "/manager/regions", labelKey: "regions" },
      { to: "/manager/locations", labelKey: "locations" },
      { to: "/manager/special-hours", labelKey: "specialHours" },
    ],
  },
  {
    labelKey: "groupRoles",
    children: [
      { to: "/manager/roles", labelKey: "roles" },
      { to: "/manager/role-equivalents", labelKey: "roleEquivalents" },
    ],
  },
  {
    labelKey: "groupPeople",
    children: [
      { to: "/manager/quick-entry", labelKey: "quickEntry" },
      { to: "/manager/employees", labelKey: "employees" },
      { to: "/manager/employee-onboarding", labelKey: "employeeOnboarding" },
      {
        to: "/manager/employee-availability",
        labelKey: "employeeAvailability",
        requiresEmployees: true,
      },
    ],
  },
  {
    labelKey: "groupSchedulingRules",
    children: [
      {
        to: "/manager/employee-association",
        labelKey: "employeeAssociation",
        requiresEmployees: true,
      },
      { to: "/manager/hour-restrictions", labelKey: "hourRestrictions" },
      { to: "/manager/day-blackouts", labelKey: "dayBlackouts" },
      { to: "/manager/day-preferences", labelKey: "dayPreferences" },
      { to: "/manager/hour-range-preferences", labelKey: "hourRangePreferences" },
      { to: "/manager/frequency-caps", labelKey: "frequencyCaps" },
    ],
  },
  {
    labelKey: "groupScheduling",
    children: [
      { to: "/manager/shift-templates", labelKey: "shiftTemplates" },
      { to: "/manager/schedule", labelKey: "schedule" },
      { to: "/manager/approved-schedules", labelKey: "approvedSchedules" },
      { to: "/manager/export-schedules", labelKey: "exportSchedules" },
    ],
  },
  {
    labelKey: "groupCheckIn",
    children: [
      { to: "/manager/check-in-qr", labelKey: "checkInQr" },
      { to: "/manager/check-in-report", labelKey: "checkInReport" },
      { to: "/manager/payroll", labelKey: "payroll" },
    ],
  },
  { to: "/manager/data-privacy", labelKey: "dataPrivacy" },
];

export const employeeNav: NavEntry[] = [
  { to: "/employee/availability", labelKey: "myAvailability" },
  { to: "/employee/check-in", labelKey: "checkIn" },
  { to: "/employee/data-privacy", labelKey: "dataPrivacy" },
];

/**
 * The manager entries this manager should see: company-wide pages
 * (utils/permissions ADMIN_ONLY_PATHS) are dropped for regular managers,
 * items needing employees wait for the first one, and a group left with no
 * children is dropped.
 */
export function visibleManagerNav(
  access: Pick<ManagerAccess, "isAdmin">,
  hasEmployees: boolean,
): NavEntry[] {
  const show = (item: NavItem) =>
    (access.isAdmin || !isAdminOnlyPath(item.to)) &&
    (!item.requiresEmployees || hasEmployees);
  const out: NavEntry[] = [];
  for (const entry of managerNav) {
    if (isGroup(entry)) {
      const children = entry.children.filter(show);
      if (children.length > 0) out.push({ ...entry, children });
    } else if (show(entry)) {
      out.push(entry);
    }
  }
  return out;
}
