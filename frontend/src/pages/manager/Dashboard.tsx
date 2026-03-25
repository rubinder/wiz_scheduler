import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import Import7ShiftsModal from "../../components/shared/Import7ShiftsModal";

const navCards = [
  {
    to: "/manager/company",
    title: "Company",
    desc: "View and edit company settings",
  },
  {
    to: "/manager/regions",
    title: "Regions",
    desc: "Manage geographic regions",
  },
  {
    to: "/manager/locations",
    title: "Locations",
    desc: "Manage store locations",
  },
  { to: "/manager/roles", title: "Roles", desc: "Define employee roles" },
  {
    to: "/manager/employees",
    title: "Employees",
    desc: "Manage your workforce",
  },
  {
    to: "/manager/shift-templates",
    title: "Shift Templates",
    desc: "Configure weekly shift patterns",
  },
  {
    to: "/manager/schedule",
    title: "Schedule",
    desc: "Generate and approve schedules",
  },
];

export default function Dashboard() {
  const { user } = useAuth();
  const [showImport, setShowImport] = useState(false);

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-2xl font-bold">
          Welcome, {user?.full_name ?? "Manager"}
        </h1>
        <button
          onClick={() => setShowImport(true)}
          className="px-4 py-2 bg-orange-600 text-white rounded hover:bg-orange-700 text-sm font-medium"
        >
          Import from 7shifts
        </button>
      </div>
      <p className="text-gray-600 mb-8">
        Manage your scheduling from the dashboard below.
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {navCards.map((card) => (
          <Link
            key={card.to}
            to={card.to}
            className="block bg-white rounded-lg shadow hover:shadow-md transition-shadow p-6 border border-gray-200"
          >
            <h3 className="text-lg font-semibold text-indigo-600 mb-1">
              {card.title}
            </h3>
            <p className="text-sm text-gray-500">{card.desc}</p>
          </Link>
        ))}
      </div>

      {showImport && (
        <Import7ShiftsModal onClose={() => setShowImport(false)} />
      )}
    </div>
  );
}
