import { Link } from "react-router-dom";
import { useLanguage } from "../../i18n/LanguageContext";
import { marketing as m } from "../../theme";

export default function MarketingFooter() {
  const { t } = useLanguage();

  const links = [
    { to: "/features", label: t.landing.featuresLink },
    { to: "/privacy-policy", label: t.gdpr.privacyPolicy },
    { to: "/terms", label: t.gdpr.termsOfService },
    { to: "/dpa", label: t.gdpr.dpa },
  ];

  return (
    <footer className={`border-t ${m.rule.heavy} mt-24`}>
      <div className="max-w-[92rem] mx-auto px-6 py-10 flex flex-col gap-6 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="w-5 h-5" />
          <span className={`${m.text.meta} !text-ink`}>{t.common.appName}</span>
        </div>
        <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
          {links.map((l) => (
            <Link key={l.to} to={l.to} className={`${m.btn.link} text-sm`}>
              {l.label}
            </Link>
          ))}
        </div>
        <span className={m.text.meta}>Suggestival LLC</span>
      </div>
    </footer>
  );
}
