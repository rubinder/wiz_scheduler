import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getTermsOfService, type PolicyDocument } from "../api/gdpr";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { text, action } from "../theme";

export default function TermsOfService() {
  const { t } = useLanguage();
  useDocumentTitle("Terms of Service");
  const [policy, setPolicy] = useState<PolicyDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getTermsOfService()
      .then(setPolicy)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load terms"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="glass-card p-8 w-full max-w-4xl">
        <h1 className={`text-2xl font-bold text-center mb-2 ${text.heading}`}>
          {t.gdpr.termsOfService}
        </h1>

        {loading && <p className={`${text.muted} text-center`}>{t.common.loading}</p>}

        {error && <div className="glass-alert-error mb-4">{error}</div>}

        {policy && (
          <>
            <div className={`flex justify-between text-sm ${text.muted} mb-6`}>
              <span>{t.gdpr.version}: {policy.version}</span>
              <span>{t.gdpr.effectiveDate}: {policy.effective_date}</span>
            </div>
            <div className="max-h-[60vh] overflow-y-auto pr-2">
              <p className="text-gray-300 whitespace-pre-wrap">{policy.content}</p>
            </div>
          </>
        )}

        <div className="mt-6 text-center">
          <Link to="/login" className={`${action.link} text-sm`}>
            {t.gdpr.backToLogin}
          </Link>
        </div>
      </div>
    </div>
  );
}
