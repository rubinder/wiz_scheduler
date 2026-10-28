import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import * as companyApi from "../../api/company";
import type { Company } from "../../types";
import LanguageSelector from "../shared/LanguageSelector";
import FeedbackForm from "../FeedbackForm";
import { text, bg, border } from "../../theme";

interface TopBarProps {
  /** Opens the sidebar drawer; the button only shows below md (#215). */
  onMenuClick: () => void;
}

export default function TopBar({ onMenuClick }: TopBarProps) {
  const { user, logout, switchCompany } = useAuth();
  const { t } = useLanguage();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [switching, setSwitching] = useState(false);
  const [showFeedback, setShowFeedback] = useState(false);

  useEffect(() => {
    if (user?.ownership_group_id && user.user_role === "manager") {
      companyApi.listGroupCompanies().then(setCompanies).catch(() => {});
    }
  }, [user?.ownership_group_id, user?.user_role]);

  const handleSwitch = useCallback(
    async (companyId: string) => {
      if (companyId === user?.company_id || switching) return;
      setSwitching(true);
      try {
        await switchCompany(companyId);
        window.location.reload();
      } catch {
        setSwitching(false);
      }
    },
    [user?.company_id, switching, switchCompany]
  );

  const currentCompany = companies.find((c) => c.id === user?.company_id);

  return (
    // A flex row in document order, never a physical offset: in RTL the
    // sidebar sits on the right and a fixed `left` put these controls on
    // top of it (#215).
    <header className={`min-h-14 ${bg.topbar} border-b ${border.default} flex items-center gap-3 px-4 md:px-6 py-2`}>
      <button
        type="button"
        onClick={onMenuClick}
        aria-label={t.common.openMenu}
        aria-controls="app-sidebar"
        className={`md:hidden -ms-1 p-1.5 rounded ${text.primary} hover:opacity-80`}
      >
        <svg aria-hidden="true" viewBox="0 0 24 24" className="w-6 h-6" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round">
          <path d="M4 6h16M4 12h16M4 18h16" />
        </svg>
      </button>
      <h2 className={`text-lg font-semibold ${text.primary} whitespace-nowrap`}>{t.common.appName}</h2>
      <div className="ms-auto flex flex-wrap items-center justify-end gap-x-4 gap-y-1 min-w-0">
        {companies.length > 1 && (
          <select
            value={user?.company_id ?? ""}
            onChange={(e) => handleSwitch(e.target.value)}
            disabled={switching}
            className="glass-input-sm"
          >
            {companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
        {companies.length <= 1 && currentCompany && (
          <span className={`hidden sm:inline text-sm ${text.muted}`}>{currentCompany.name}</span>
        )}
        {user && (
          <span className={`hidden sm:inline text-sm ${text.secondary}`}>
            {user.full_name ?? user.email}
          </span>
        )}
        <LanguageSelector />
        {user?.user_role === "manager" && (
          <button
            onClick={() => setShowFeedback(true)}
            className="text-sm text-blue-400 hover:text-blue-300 font-medium transition-colors"
            title="Send feedback or feature request"
          >
            Feedback
          </button>
        )}
        <button
          onClick={logout}
          className="text-sm text-red-400 hover:text-red-300 font-medium transition-colors"
        >
          {t.common.logout}
        </button>
      </div>
      {showFeedback && <FeedbackForm onClose={() => setShowFeedback(false)} />}
    </header>
  );
}
