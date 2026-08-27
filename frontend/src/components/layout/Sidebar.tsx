import { useEffect, useState } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { listEmployees } from "../../api/employees";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, nav } from "../../theme";

interface NavItem {
  to: string;
  labelKey: string;
  /** Hidden until the company has at least one employee. */
  requiresEmployees?: boolean;
}

interface NavGroup {
  labelKey: string;
  children: NavItem[];
}

type NavEntry = NavItem | NavGroup;

function isGroup(entry: NavEntry): entry is NavGroup {
  return (entry as NavGroup).children !== undefined;
}

const managerNav: NavEntry[] = [
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
    ],
  },
  { to: "/manager/data-privacy", labelKey: "dataPrivacy" },
];

const employeeNav: NavEntry[] = [
  { to: "/employee/availability", labelKey: "myAvailability" },
  { to: "/employee/check-in", labelKey: "checkIn" },
  { to: "/employee/data-privacy", labelKey: "dataPrivacy" },
];

export default function Sidebar() {
  const { user } = useAuth();
  const { t } = useLanguage();
  const { pathname } = useLocation();
  const isManager = user?.user_role === "manager";
  const [hasEmployees, setHasEmployees] = useState(false);
  const [openGroups, setOpenGroups] = useState<Record<string, boolean>>({});

  useEffect(() => {
    if (!isManager) return;
    listEmployees()
      .then((emps) => setHasEmployees(emps.length > 0))
      .catch(() => setHasEmployees(false));
  }, [isManager]);

  const entries = isManager ? managerNav : employeeNav;

  // Auto-expand whichever group owns the active route, so a deep link never
  // lands the user inside a collapsed tree. Only ever opens a group — it must
  // not re-close one the user opened by hand.
  useEffect(() => {
    const owner = entries.find(
      (e) => isGroup(e) && e.children.some((c) => pathname === c.to),
    );
    if (owner) {
      setOpenGroups((prev) => ({ ...prev, [(owner as NavGroup).labelKey]: true }));
    }
  }, [pathname, entries]);

  const label = (key: string) => t.nav[key as keyof typeof t.nav];
  const visible = (item: NavItem) => !item.requiresEmployees || hasEmployees;

  const linkClass = ({ isActive }: { isActive: boolean }) =>
    `block px-3 py-2 rounded-lg text-sm font-medium transition-all ${
      isActive ? nav.active : nav.inactive
    }`;

  return (
    <aside className={`w-64 ${bg.sidebar} border-r ${border.default} ${text.primary} min-h-screen flex flex-col`}>
      <div className={`p-5 border-b ${border.default}`}>
        <h1 className="text-xl font-bold tracking-wide flex items-center gap-2">{t.common.appName} <img src="/favicon.svg" alt="" className="w-7 h-7" /></h1>
      </div>
      <nav className="flex-1 p-4 space-y-1">
        {entries.map((entry) => {
          if (!isGroup(entry)) {
            return (
              <NavLink key={entry.to} to={entry.to} className={linkClass}>
                {label(entry.labelKey)}
              </NavLink>
            );
          }

          const children = entry.children.filter(visible);
          if (children.length === 0) return null;

          const isOpen = openGroups[entry.labelKey] ?? false;
          return (
            <div key={entry.labelKey}>
              <button
                type="button"
                aria-expanded={isOpen}
                onClick={() =>
                  setOpenGroups((prev) => ({ ...prev, [entry.labelKey]: !isOpen }))
                }
                className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-sm font-medium transition-all ${nav.inactive}`}
              >
                <span>{label(entry.labelKey)}</span>
                <span aria-hidden="true" className="text-xs">{isOpen ? "▾" : "▸"}</span>
              </button>
              {isOpen && (
                <div className={`ms-3 ps-2 border-s ${border.default} space-y-1 mt-1`}>
                  {children.map((child) => (
                    <NavLink key={child.to} to={child.to} className={linkClass}>
                      {label(child.labelKey)}
                    </NavLink>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </nav>
      {user && (
        <div className={`p-4 border-t ${border.default} text-sm ${text.muted}`}>
          {user.full_name ?? user.email}
        </div>
      )}
    </aside>
  );
}
