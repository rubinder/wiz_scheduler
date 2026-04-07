import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import Import7ShiftsModal from "../../components/shared/Import7ShiftsModal";
import { useLanguage } from "../../i18n/LanguageContext";

export default function Dashboard() {
  const { user } = useAuth();
  const { t } = useLanguage();
  const [showImport, setShowImport] = useState(false);

  const navCards = [
    {
      to: "/manager/company",
      title: t.nav.company,
      desc: t.dashboard.companyDesc,
    },
    {
      to: "/manager/regions",
      title: t.nav.regions,
      desc: t.dashboard.regionsDesc,
    },
    {
      to: "/manager/locations",
      title: t.nav.locations,
      desc: t.dashboard.locationsDesc,
    },
    { to: "/manager/roles", title: t.nav.roles, desc: t.dashboard.rolesDesc },
    {
      to: "/manager/employees",
      title: t.nav.employees,
      desc: t.dashboard.employeesDesc,
    },
    {
      to: "/manager/shift-templates",
      title: t.nav.shiftTemplates,
      desc: t.dashboard.shiftTemplatesDesc,
    },
    {
      to: "/manager/schedule",
      title: t.nav.schedule,
      desc: t.dashboard.scheduleDesc,
    },
  ];

  return (
    <div>
      <div className="flex items-center justify-between mb-2">
        <h1 className="text-2xl font-bold text-white">
          {t.dashboard.welcome} {user?.full_name ?? t.dashboard.manager}
        </h1>
        <button
          onClick={() => setShowImport(true)}
          className="glass-btn-orange"
        >
          {t.dashboard.importFrom7shifts}
        </button>
      </div>
      <p className="text-gray-400 mb-8">
        {t.dashboard.subtitle}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {navCards.map((card) => (
          <Link
            key={card.to}
            to={card.to}
            className="block glass-card p-6 hover:bg-white/[0.1] transition-all"
          >
            <h3 className="text-lg font-semibold text-purple-400 mb-1">
              {card.title}
            </h3>
            <p className="text-sm text-gray-400">{card.desc}</p>
          </Link>
        ))}
      </div>

      {showImport && (
        <Import7ShiftsModal onClose={() => setShowImport(false)} />
      )}
    </div>
  );
}
