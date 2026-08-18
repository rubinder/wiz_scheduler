import { Link } from "react-router-dom";
import LanguageSelector from "../shared/LanguageSelector";
import { useLanguage } from "../../i18n/LanguageContext";
import { marketing as m } from "../../theme";
import MarketingFooter from "./MarketingFooter";

export default function MarketingNav() {
  const { t } = useLanguage();

  return (
    <nav className={`sticky top-0 z-50 bg-newsprint border-b ${m.rule.line}`}>
      <div className="max-w-[92rem] mx-auto px-6 h-16 flex items-center justify-between gap-6">
        <Link to="/" className="flex items-center gap-3 min-w-0 shrink-0">
          <img src="/favicon.svg" alt="" className="w-7 h-7 shrink-0" />
          <span
            className={`${m.text.display} font-display text-lg font-semibold uppercase tracking-[0.06em] hidden sm:inline truncate`}
          >
            {t.common.appName}
          </span>
        </Link>
        <div className="flex items-center gap-3 sm:gap-5 shrink-0">
          <LanguageSelector variant="rota" />
          <Link to="/login" className={`${m.btn.link} text-sm hidden sm:inline`}>
            {t.login.signIn}
          </Link>
          <Link to="/register" className={`${m.btn.primary} !px-4 !py-2 text-sm`}>
            {t.register.registerBtn}
          </Link>
        </div>
      </div>
    </nav>
  );
}

export function MarketingShell({ children }: { children: React.ReactNode }) {
  return (
    <div className={`min-h-screen ${m.page} font-body`}>
      <MarketingNav />
      {children}
      <MarketingFooter />
    </div>
  );
}
