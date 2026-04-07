import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getDpa, type PolicyDocument } from "../api/gdpr";
import { useLanguage } from "../i18n/LanguageContext";

export default function DataProcessingAgreement() {
  const { t } = useLanguage();
  const [policy, setPolicy] = useState<PolicyDocument | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    getDpa()
      .then(setPolicy)
      .catch((err) => setError(err instanceof Error ? err.message : "Failed to load DPA"))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="glass-card p-8 w-full max-w-4xl">
        <h1 className="text-2xl font-bold text-center mb-2 text-white">
          {t.gdpr.dpa}
        </h1>

        {loading && <p className="text-gray-400 text-center">{t.common.loading}</p>}

        {error && <div className="glass-alert-error mb-4">{error}</div>}

        {policy && (
          <>
            <div className="flex justify-between text-sm text-gray-400 mb-6">
              <span>{t.gdpr.version}: {policy.version}</span>
              <span>{t.gdpr.effectiveDate}: {policy.effective_date}</span>
            </div>
            <div className="max-h-[60vh] overflow-y-auto pr-2">
              <p className="text-gray-300 whitespace-pre-wrap">{policy.content}</p>
            </div>

            {policy.processors && policy.processors.length > 0 && (
              <div className="mt-6">
                <h2 className="text-lg font-semibold text-white mb-3">{t.gdpr.processors}</h2>
                <div className="overflow-x-auto">
                  <table className="w-full text-sm text-left">
                    <thead>
                      <tr className="border-b border-white/[0.08]">
                        <th className="py-2 px-3 text-gray-400 font-medium">{t.gdpr.processorName}</th>
                        <th className="py-2 px-3 text-gray-400 font-medium">{t.gdpr.processorPurpose}</th>
                        <th className="py-2 px-3 text-gray-400 font-medium">{t.gdpr.processorLocation}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {policy.processors.map((proc, idx) => (
                        <tr key={idx} className="border-b border-white/[0.05]">
                          <td className="py-2 px-3 text-gray-300">{proc.name}</td>
                          <td className="py-2 px-3 text-gray-300">{proc.purpose}</td>
                          <td className="py-2 px-3 text-gray-300">{proc.location}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </>
        )}

        <div className="mt-6 text-center">
          <Link to="/login" className="text-purple-400 hover:text-purple-300 text-sm">
            {t.gdpr.backToLogin}
          </Link>
        </div>
      </div>
    </div>
  );
}
