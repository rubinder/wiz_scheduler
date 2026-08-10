import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useAuth } from "../../hooks/useAuth";
import { usePlan } from "../../hooks/usePlan";
import Import7ShiftsModal from "../../components/shared/Import7ShiftsModal";
import ImportDeputyModal from "../../components/shared/ImportDeputyModal";
import { useLanguage } from "../../i18n/LanguageContext";
import { text, bg } from "../../theme";
import * as billingApi from "../../api/billing";
import { googleLinkCurrent } from "../../api/googleAuth";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: Record<string, unknown>) => void;
          renderButton: (el: HTMLElement, config: Record<string, unknown>) => void;
        };
      };
    };
  }
}

function LinkGoogleCard() {
  const { t } = useLanguage();
  const btnRef = useRef<HTMLDivElement>(null);
  const [error, setError] = useState("");
  const [linking, setLinking] = useState(false);

  useEffect(() => {
    const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID;
    if (!clientId || !window.google?.accounts?.id) return;

    const handleCallback = async (resp: { credential?: string }) => {
      if (!resp.credential) return;
      setError("");
      setLinking(true);
      try {
        await googleLinkCurrent(resp.credential);
        // Reload so useAuth re-fetches /me and the card disappears.
        window.location.reload();
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : t.linkGoogle.failed);
      } finally {
        setLinking(false);
      }
    };

    window.google.accounts.id.initialize({
      client_id: clientId,
      callback: handleCallback,
    });

    if (btnRef.current) {
      window.google.accounts.id.renderButton(btnRef.current, {
        theme: "filled_black",
        size: "large",
        text: "continue_with",
      });
    }
  }, [t.linkGoogle.failed]);

  return (
    <div className="glass-card p-4 mb-6 flex flex-col sm:flex-row sm:items-center gap-4">
      <div className="flex-1">
        <h3 className={`text-base font-semibold ${text.body} mb-1`}>
          {t.linkGoogle.title}
        </h3>
        <p className={`text-sm ${text.muted}`}>{t.linkGoogle.description}</p>
        {error && (
          <p className="text-sm text-red-400 mt-2">{error}</p>
        )}
      </div>
      <div className="flex flex-col items-end gap-1">
        <div ref={btnRef} aria-disabled={linking} />
        {linking && (
          <span className={`text-xs ${text.muted}`}>{t.linkGoogle.linking}</span>
        )}
      </div>
    </div>
  );
}

export default function Dashboard() {
  const { user } = useAuth();
  const { t } = useLanguage();
  const [showImport, setShowImport] = useState(false);
  const [showDeputyImport, setShowDeputyImport] = useState(false);

  // Fail-open per usePlan's contract: `plan` stays null while loading or
  // if the fetch failed, and `plan?.plan === "free"` is then false — so
  // a plan-fetch outage never blocks these importers. The API still
  // enforces this server-side (assert_paid_plan) regardless.
  const { plan } = usePlan();
  const importsDisabled = plan?.plan === "free";

  const [searchParams, setSearchParams] = useSearchParams();
  const [sessionConfirmError, setSessionConfirmError] = useState("");

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
        setSessionConfirmError(
          err instanceof Error ? err.message : t.dashboard.upgradeConfirmFailed
        );
      });
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Safety net for any already-issued upgrade Checkout link that still
  // points at the dashboard (the primary handler now lives on
  // /manager/schedule — see Schedule.tsx). Strips the query param in a
  // `finally` so a page reload can't retry confirmation forever.
  useEffect(() => {
    const sessionId = searchParams.get("upgrade_session_id");
    if (!sessionId) return;
    billingApi
      .confirmUpgrade(sessionId)
      .then(() => {
        window.location.reload();
      })
      .catch((err) => {
        console.error("Upgrade confirmation failed", err);
        setSessionConfirmError(
          err instanceof Error ? err.message : t.dashboard.upgradeConfirmFailed
        );
      })
      .finally(() => {
        searchParams.delete("upgrade_session_id");
        setSearchParams(searchParams, { replace: true });
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
            disabled={importsDisabled}
            title={importsDisabled ? t.dashboard.importDisabledFreePlan : undefined}
            className="glass-btn-orange"
          >
            {t.dashboard.importFrom7shifts}
          </button>
          <button
            onClick={() => setShowDeputyImport(true)}
            disabled={importsDisabled}
            title={importsDisabled ? t.dashboard.importDisabledFreePlan : undefined}
            className="glass-btn-primary"
          >
            {t.dashboard.importFromDeputy}
          </button>
        </div>
      </div>
      {sessionConfirmError && (
        <div className="glass-alert-error mb-4">
          {sessionConfirmError}
        </div>
      )}
      <p className={`${text.muted} ${importsDisabled ? "mb-1" : "mb-8"}`}>
        {t.dashboard.subtitle}
      </p>
      {importsDisabled && (
        <p className={`text-xs ${text.muted} mb-8`}>
          {t.dashboard.importDisabledFreePlan}
        </p>
      )}
      {user && !user.has_google && <LinkGoogleCard />}
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
