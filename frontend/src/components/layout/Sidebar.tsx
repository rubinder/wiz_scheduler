import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { listEmployees } from "../../api/employees";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg, border, nav } from "../../theme";

interface NavItem {
  to: string;
  labelKey: string;
}

const baseManagerLinks: NavItem[] = [
  { to: "/manager/dashboard", labelKey: "dashboard" },
  { to: "/manager/company", labelKey: "company" },
  { to: "/manager/team", labelKey: "team" },
  { to: "/manager/regions", labelKey: "regions" },
  { to: "/manager/locations", labelKey: "locations" },
  { to: "/manager/special-hours", labelKey: "specialHours" },
  { to: "/manager/roles", labelKey: "roles" },
  { to: "/manager/role-equivalents", labelKey: "roleEquivalents" },
  { to: "/manager/employees", labelKey: "employees" },
  { to: "/manager/hour-restrictions", labelKey: "hourRestrictions" },
  { to: "/manager/day-blackouts", labelKey: "dayBlackouts" },
  { to: "/manager/employee-onboarding", labelKey: "employeeOnboarding" },
];

const postEmployeeManagerLinks: NavItem[] = [
  { to: "/manager/employee-availability", labelKey: "employeeAvailability" },
  { to: "/manager/employee-association",  labelKey: "employeeAssociation" },
  { to: "/manager/shift-templates",        labelKey: "shiftTemplates" },
  { to: "/manager/schedule",               labelKey: "schedule" },
  { to: "/manager/export-schedules",       labelKey: "exportSchedules" },
  { to: "/manager/data-privacy",           labelKey: "dataPrivacy" },
];

const employeeLinks: NavItem[] = [
  { to: "/employee/availability", labelKey: "myAvailability" },
  { to: "/employee/data-privacy", labelKey: "dataPrivacy" },
];

export default function Sidebar() {
  const { user } = useAuth();
  const { t } = useLanguage();
  const isManager = user?.user_role === "manager";
  const [hasEmployees, setHasEmployees] = useState(false);

  useEffect(() => {
    if (!isManager) return;
    listEmployees()
      .then((emps) => setHasEmployees(emps.length > 0))
      .catch(() => setHasEmployees(false));
  }, [isManager]);

  const links = isManager
    ? hasEmployees
      ? [...baseManagerLinks, ...postEmployeeManagerLinks]
      : [...baseManagerLinks, ...postEmployeeManagerLinks.slice(2)]
    : employeeLinks;

  return (
    <aside className={`w-64 ${bg.sidebar} border-r ${border.default} ${text.primary} min-h-screen flex flex-col`}>
      <div className={`p-5 border-b ${border.default}`}>
        <h1 className="text-xl font-bold tracking-wide flex items-center gap-2">{t.common.appName} <img src="/favicon.svg" alt="" className="w-7 h-7" /></h1>
      </div>
      <nav className="flex-1 p-4 space-y-1">
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            className={({ isActive }) =>
              `block px-3 py-2 rounded-lg text-sm font-medium transition-all ${
                isActive
                  ? nav.active
                  : nav.inactive
              }`
            }
          >
            {t.nav[link.labelKey as keyof typeof t.nav]}
          </NavLink>
        ))}
      </nav>
      {user && (
        <div className={`p-4 border-t ${border.default} text-sm ${text.muted}`}>
          {user.full_name ?? user.email}
        </div>
      )}
    </aside>
  );
}
