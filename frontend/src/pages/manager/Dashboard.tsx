import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import Import7ShiftsModal from "../../components/shared/Import7ShiftsModal";
import ImportDeputyModal from "../../components/shared/ImportDeputyModal";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg } from "../../theme";
import * as billingApi from "../../api/billing";

export default function Dashboard() {
  const { user } = useAuth();
  const { t } = useLanguage();
  const [showImport, setShowImport] = useState(false);
  const [showDeputyImport, setShowDeputyImport] = useState(false);

  const [searchParams, setSearchParams] = useSearchParams();

  useEffect(() => {
    const sessionId = searchParams.get("reactivate_session_id");
    if (!sessionId) return;
    searchParams.delete("reactivate_session_id");
    setSearchParams(searchParams, { replace: true });

    billingApi
      .confirmReactivation(sessionId)
      .then(() => {
        window.location.reload();
      })
      .catch((err) => {
        console.error("Reactivation confirmation failed", err);
      });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
        <h1 className={`text-2xl font-bold ${text.heading}`}>
          {t.dashboard.welcome} {user?.full_name ?? t.dashboard.manager}
        </h1>
        <div className="flex gap-2">
          <button
            onClick={() => setShowImport(true)}
            className="glass-btn-orange"
          >
            {t.dashboard.importFrom7shifts}
          </button>
          <button
            onClick={() => setShowDeputyImport(true)}
            className="glass-btn-primary"
          >
            {t.dashboard.importFromDeputy}
          </button>
        </div>
      </div>
      <p className={`${text.muted} mb-8`}>
        {t.dashboard.subtitle}
      </p>
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6">
        {navCards.map((card) => (
          <Link
            key={card.to}
            to={card.to}
            className={`block glass-card p-6 ${bg.cardHover} transition-all`}
          >
            <h3 className="text-lg font-semibold text-accent mb-1">
              {card.title}
            </h3>
            <p className={`text-sm ${text.muted}`}>{card.desc}</p>
          </Link>
        ))}
      </div>

      {showImport && (
        <Import7ShiftsModal
          onClose={() => setShowImport(false)}
          onSuccess={() => window.location.reload()}
        />
      )}
      {showDeputyImport && (
        <ImportDeputyModal
          onClose={() => setShowDeputyImport(false)}
          onSuccess={() => window.location.reload()}
        />
      )}
    </div>
  );
}
