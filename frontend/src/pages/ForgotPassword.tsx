import { useState } from "react";
import { Link } from "react-router-dom";
import { forgotPassword } from "../api/auth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { text, action } from "../theme";

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
      <div className="min-h-screen flex items-center justify-center">
        <div className="glass-card w-full max-w-md p-8 text-center">
          <h1 className={`text-xl font-bold mb-2 ${text.heading}`}>
            {t.forgotPassword.confirmTitle}
          </h1>
          <p className={`text-sm ${text.muted} mb-6`}>
            {t.forgotPassword.confirmBody}
          </p>
          <Link to="/login" className={`${action.link} text-sm`}>
            {t.forgotPassword.backToLogin}
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="glass-card w-full max-w-md p-8">
        <h1 className={`text-2xl font-bold mb-1 ${text.heading}`}>
          {t.forgotPassword.title}
        </h1>
        <p className={`text-sm ${text.muted} mb-6`}>
          {t.forgotPassword.description}
        </p>

        {error && <div className="glass-alert-error mb-4">{error}</div>}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="glass-label">{t.common.email}</label>
            <input
              type="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="glass-input w-full"
              placeholder={t.forgotPassword.emailPlaceholder}
            />
          </div>
          <button
            type="submit"
            disabled={submitting || !email.trim()}
            className="glass-btn-primary w-full disabled:opacity-50"
          >
            {submitting ? t.forgotPassword.submitting : t.forgotPassword.submit}
          </button>
        </form>

        <p className={`text-sm ${text.muted} mt-6 text-center`}>
          <Link to="/login" className={action.link}>
            {t.forgotPassword.backToLogin}
          </Link>
        </p>
      </div>
    </div>
  );
}
