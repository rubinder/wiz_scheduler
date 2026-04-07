import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import * as companyApi from "../../api/company";
import type { Company } from "../../types";
import LanguageSelector from "../shared/LanguageSelector";

export default function TopBar() {
  const { user, logout, switchCompany } = useAuth();
  const { t } = useLanguage();
  const [companies, setCompanies] = useState<Company[]>([]);
  const [switching, setSwitching] = useState(false);

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
    <header className="h-14 bg-white/[0.04] backdrop-blur-xl border-b border-white/[0.06] relative flex items-center px-6">
      <h2 className="text-lg font-semibold text-gray-200">{t.common.appName}</h2>
      <div
        className="flex items-center gap-4"
        style={{ position: "absolute", left: 294 }}
      >
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
          <span className="text-sm text-gray-400">{currentCompany.name}</span>
        )}
        {user && (
          <span className="text-sm text-gray-300">
            {user.full_name ?? user.email}
          </span>
        )}
        <LanguageSelector />
        <button
          onClick={logout}
          className="text-sm text-red-400 hover:text-red-300 font-medium transition-colors"
        >
          {t.common.logout}
        </button>
      </div>
    </header>
  );
}
