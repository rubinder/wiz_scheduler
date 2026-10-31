import { useState } from "react";
import { Link } from "react-router-dom";
import { retrievePayrollArchive } from "../api/billing";
import { ApiError } from "../api/client";
import AuthLayout from "../components/marketing/AuthLayout";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { marketing as m } from "../theme";

/**
 * Public "Retrieve payroll archive" page (#218). The account behind a paid
 * archive is gone, so there is nothing to sign in to: a recipient enters
 * their address and, if it matches, gets a single-use link by email. The
 * page shows the same message either way, exactly as the server does.
 */
export default function PayrollArchiveRetrieve() {
  useDocumentTitle("Retrieve Payroll Archive");
  const { t } = useLanguage();
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await retrievePayrollArchive(email.trim());
      setSubmitted(true);
    } catch (err: unknown) {
      setError(
        err instanceof ApiError && err.status === 429
          ? t.payrollArchive.rateLimited
          : t.payrollArchive.failed
      );
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <AuthLayout title={t.payrollArchive.confirmTitle}>
        <p className={`text-sm ${m.text.muted} mb-6`}>{t.payrollArchive.confirmBody}</p>
        <Link to="/login" className={`${m.btn.link} text-sm`}>
          {t.payrollArchive.backToLogin}
        </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title={t.payrollArchive.retrieveTitle}>
      <p className={`text-sm ${m.text.muted} mb-6`}>{t.payrollArchive.retrieveDescription}</p>

      {error && <div className={`${m.alert.error} mb-4`}>{error}</div>}

      <form onSubmit={handleSubmit} className="space-y-4">
        <div>
          <label className={m.label} htmlFor="payroll-archive-email">
            {t.common.email}
          </label>
          <input
            id="payroll-archive-email"
            type="email"
            required
            autoFocus
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className={m.input}
            placeholder={t.payrollArchive.emailPlaceholder}
          />
        </div>
        <button
          type="submit"
          disabled={submitting || !email.trim()}
          className={`${m.btn.primary} w-full`}
        >
          {submitting ? t.payrollArchive.submitting : t.payrollArchive.submit}
        </button>
      </form>

      <p className={`text-sm ${m.text.muted} mt-6 text-center`}>
        <Link to="/login" className={m.btn.link}>
          {t.payrollArchive.backToLogin}
        </Link>
      </p>
    </AuthLayout>
  );
}
