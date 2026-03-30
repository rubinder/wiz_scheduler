import { useEffect, useState } from "react";
import { NavLink } from "react-router-dom";
import { listEmployees } from "../../api/employees";
import { useAuth } from "../../hooks/useAuth";

interface NavItem {
  to: string;
  label: string;
}

const baseManagerLinks: NavItem[] = [
  { to: "/manager/dashboard", label: "Dashboard" },
  { to: "/manager/company", label: "Company" },
  { to: "/manager/regions", label: "Regions" },
  { to: "/manager/locations", label: "Locations" },
  { to: "/manager/roles", label: "Roles" },
  { to: "/manager/role-equivalents", label: "Role Equivalents" },
  { to: "/manager/employees", label: "Employees" },
  { to: "/manager/employee-onboarding", label: "Employee Onboarding" },
];

const postEmployeeManagerLinks: NavItem[] = [
  { to: "/manager/employee-association", label: "Employee Availability & Association" },
  { to: "/manager/shift-templates", label: "Shift Templates" },
  { to: "/manager/schedule", label: "Schedule" },
  { to: "/manager/export-schedules", label: "Export Approved Schedules" },
];

const employeeLinks: NavItem[] = [
  { to: "/employee/availability", label: "My Availability" },
];

export default function Sidebar() {
  const { user } = useAuth();
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
      : [...baseManagerLinks, ...postEmployeeManagerLinks.slice(1)]
    : employeeLinks;

  return (
    <aside className="w-64 bg-gray-900 text-white min-h-screen flex flex-col">
      <div className="p-5 border-b border-gray-700">
        <h1 className="text-xl font-bold tracking-wide flex items-center gap-2">Wiz Scheduler <img src="/favicon.svg" alt="" className="w-7 h-7" /></h1>
      </div>
      <nav className="flex-1 p-4 space-y-1">
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            className={({ isActive }) =>
              `block px-3 py-2 rounded text-sm font-medium transition-colors ${
                isActive
                  ? "bg-indigo-600 text-white"
                  : "text-gray-300 hover:bg-gray-800 hover:text-white"
              }`
            }
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
      {user && (
        <div className="p-4 border-t border-gray-700 text-sm text-gray-400">
          {user.full_name ?? user.email}
        </div>
      )}
    </aside>
  );
}
