import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getPrivacyPolicy, type PolicyDocument } from "../api/gdpr";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { MarketingShell } from "../components/marketing/MarketingNav";
import { marketing as m } from "../theme";

export default function PrivacyPolicy() {
  const { t } = useLanguage();
  useDocumentTitle("Privacy Policy");
  const [policy, setPolicy] = useState<PolicyDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getPrivacyPolicy()
      .then(setPolicy)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load policy"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <MarketingShell>
      <div className="max-w-4xl mx-auto px-6 py-16">
        <div className={`${m.surface} border ${m.rule.line} p-8`}>
          <h1 className={`${m.text.display} font-display text-2xl font-semibold text-center mb-2`}>
            {t.gdpr.privacyPolicy}
          </h1>

          {loading && <p className={`${m.text.muted} text-center`}>{t.common.loading}</p>}

          {error && <div className={m.alert.error}>{error}</div>}

          {policy && (
            <>
              <div className={`flex justify-between text-sm ${m.text.muted} mb-6`}>
                <span>{t.gdpr.version}: {policy.version}</span>
                <span>{t.gdpr.effectiveDate}: {policy.effective_date}</span>
              </div>
              <div className="max-h-[60vh] overflow-y-auto pe-2 max-w-[68ch]">
                <p className={`${m.text.body} whitespace-pre-wrap`}>{policy.content}</p>
              </div>
            </>
          )}

          <div className="mt-6 text-center">
            <Link to="/login" className={`${m.btn.link} text-sm`}>
              {t.gdpr.backToLogin}
            </Link>
          </div>
        </div>
      </div>
    </MarketingShell>
  );
}
