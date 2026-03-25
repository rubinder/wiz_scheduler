import { useCallback, useEffect, useState } from "react";
import { useAuth } from "../../hooks/useAuth";
import * as companyApi from "../../api/company";
import type { Company } from "../../types";

export default function TopBar() {
  const { user, logout, switchCompany } = useAuth();
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

  // Align controls at a fixed viewport position (sidebar=256px, target=550px from viewport)
  // Header starts at 256px, has px-6 (24px) padding, so left = 550 - 256 = 294px from header edge
  return (
    <header className="h-14 bg-white border-b border-gray-200 relative flex items-center px-6">
      <h2 className="text-lg font-semibold text-gray-800">Wiz Scheduler</h2>
      <div
        className="flex items-center gap-4"
        style={{ position: "absolute", left: 294 }}
      >
        {companies.length > 1 && (
          <select
            value={user?.company_id ?? ""}
            onChange={(e) => handleSwitch(e.target.value)}
            disabled={switching}
            className="text-sm border border-gray-300 rounded px-2 py-1 bg-white"
          >
            {companies.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        )}
        {companies.length <= 1 && currentCompany && (
          <span className="text-sm text-gray-500">{currentCompany.name}</span>
        )}
        {user && (
          <span className="text-sm text-gray-600">
            {user.full_name ?? user.email}
          </span>
        )}
        <button
          onClick={logout}
          className="text-sm text-red-600 hover:text-red-800 font-medium"
        >
          Logout
        </button>
      </div>
    </header>
  );
}
