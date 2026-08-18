import { useState } from "react";
import { Link } from "react-router-dom";
import { forgotPassword } from "../api/auth";
import AuthLayout from "../components/marketing/AuthLayout";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { marketing as m } from "../theme";

export default function ForgotPassword() {
  useDocumentTitle("Forgot Password");
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
      await forgotPassword(email);
      setSubmitted(true);
    } catch (err: unknown) {
      // The endpoint always returns 204, so any error here is network /
      // serialization. Show generic copy.
      setError(err instanceof Error ? err.message : t.forgotPassword.failed);
    } finally {
      setSubmitting(false);
    }
  };

  if (submitted) {
    return (
      <AuthLayout title={t.forgotPassword.confirmTitle}>
          <p className={`text-sm ${m.text.muted} mb-6`}>
            {t.forgotPassword.confirmBody}
          </p>
          <Link to="/login" className={`${m.btn.link} text-sm`}>
            {t.forgotPassword.backToLogin}
          </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title={t.forgotPassword.title}>
        <p className={`text-sm ${m.text.muted} mb-6`}>
          {t.forgotPassword.description}
        </p>

        {error && <div className={`${m.alert.error} mb-4`}>{error}</div>}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className={m.label}>{t.common.email}</label>
            <input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className={m.input}
              placeholder={t.forgotPassword.emailPlaceholder}
            />
          </div>
          <button
            type="submit"
            disabled={submitting || !email.trim()}
            className={`${m.btn.primary} w-full`}
          >
            {submitting ? t.forgotPassword.submitting : t.forgotPassword.submit}
          </button>
        </form>

        <p className={`text-sm ${m.text.muted} mt-6 text-center`}>
          <Link to="/login" className={m.btn.link}>
            {t.forgotPassword.backToLogin}
          </Link>
        </p>
    </AuthLayout>
  );
}
