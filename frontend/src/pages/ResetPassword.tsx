import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError } from "../api/client";
import { resetPassword } from "../api/auth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { text, action } from "../theme";

export default function ResetPassword() {
  useDocumentTitle("Reset Password");
  const { t } = useLanguage();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);

  if (!token) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="glass-card w-full max-w-md p-8 text-center">
          <h1 className="text-xl font-bold text-red-400 mb-2">
            {t.resetPassword.invalidLink}
          </h1>
          <p className={text.muted}>{t.resetPassword.invalidLinkDesc}</p>
          <Link to="/login" className={`mt-4 inline-block ${action.link} text-sm`}>
            {t.resetPassword.backToLogin}
          </Link>
        </div>
      </div>
    );
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    if (password.length < 6) {
      setError(t.resetPassword.passwordMin);
      return;
    }
    if (password !== confirm) {
      setError(t.resetPassword.passwordMismatch);
      return;
    }
    setSubmitting(true);
    try {
      const result = await resetPassword(token, password);
      localStorage.setItem("token", result.access_token);
      navigate("/manager/dashboard", { replace: true });
      window.location.reload();
    } catch (err: unknown) {
      if (err instanceof ApiError) {
        if (err.status === 410) {
          setError(t.resetPassword.expired);
        } else if (err.status === 404) {
          setError(t.resetPassword.invalidLinkDesc);
        } else {
          setError(err.message);
        }
      } else {
        setError(err instanceof Error ? err.message : t.resetPassword.failed);
      }
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="glass-card w-full max-w-md p-8">
        <h1 className={`text-2xl font-bold mb-1 ${text.heading}`}>
          {t.resetPassword.title}
        </h1>
        <p className={`text-sm ${text.muted} mb-6`}>
          {t.resetPassword.description}
        </p>

        {error && <div className="glass-alert-error mb-4">{error}</div>}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="glass-label">{t.resetPassword.newPassword}</label>
            <input
              type="password"
              required
              autoFocus
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="glass-input w-full"
              placeholder={t.resetPassword.atLeast6}
            />
          </div>
          <div>
            <label className="glass-label">
              {t.resetPassword.confirmPassword}
            </label>
            <input
              type="password"
              required
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="glass-input w-full"
              placeholder={t.resetPassword.reenterPassword}
            />
          </div>
          <button
            type="submit"
            disabled={submitting || !password || !confirm}
            className="glass-btn-primary w-full disabled:opacity-50"
          >
            {submitting ? t.resetPassword.submitting : t.resetPassword.submit}
          </button>
        </form>
      </div>
    </div>
  );
}
