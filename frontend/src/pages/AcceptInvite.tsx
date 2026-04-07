import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { acceptInvite, getInviteInfo } from "../api/employees";
import type { InviteInfoResponse } from "../types";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";

export default function AcceptInvite() {
  useDocumentTitle("Accept Invite");
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const navigate = useNavigate();
  const { t } = useLanguage();

  const [info, setInfo] = useState<InviteInfoResponse | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setError(t.acceptInvite.noToken);
      setLoading(false);
      return;
    }
    getInviteInfo(token)
      .then(setInfo)
      .catch((err) => setError(err instanceof Error ? err.message : t.acceptInvite.invalidOrExpired))
      .finally(() => setLoading(false));
  }, [token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (password.length < 6) {
      setError(t.acceptInvite.passwordMin);
      return;
    }
    if (password !== confirm) {
      setError(t.acceptInvite.passwordMismatch);
      return;
    }
    if (!token) return;

    setSubmitting(true);
    try {
      const result = await acceptInvite(token, password);
      localStorage.setItem("token", result.access_token);
      navigate("/employee/availability", { replace: true });
      // Force a full reload so useAuth picks up the new token
      window.location.reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t.acceptInvite.failedCreate);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="text-gray-500">{t.common.loading}</div>
      </div>
    );
  }

  if (!info) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className="glass-card w-full max-w-md p-8 text-center">
          <h1 className="text-xl font-bold text-red-400 mb-2">{t.acceptInvite.invalidInvite}</h1>
          <p className="text-gray-400">{error || t.acceptInvite.invalidInviteDesc}</p>
          <a
            href="/login"
            className="mt-4 inline-block text-purple-400 hover:text-purple-300 text-sm"
          >
            {t.acceptInvite.goToLogin}
          </a>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="glass-card w-full max-w-md p-8">
        <h1 className="text-2xl font-bold mb-1 text-white">{t.acceptInvite.setupTitle}</h1>
        <p className="text-sm text-gray-400 mb-6">
          {t.acceptInvite.welcomeTo} <strong className="text-gray-300">{info.company_name}</strong>, {info.employee_name}!
          <br />
          {t.acceptInvite.choosePassword}
        </p>

        {error && (
          <div className="glass-alert-error mb-4">
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className="glass-label">
              {t.common.email}
            </label>
            <input
              type="email"
              value={info.email}
              disabled
              className="w-full border border-white/[0.06] rounded px-3 py-2 text-sm bg-white/[0.03] text-gray-500"
            />
          </div>
          <div>
            <label className="glass-label">
              {t.common.password}
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="glass-input w-full"
              placeholder={t.acceptInvite.atLeast6}
              autoFocus
            />
          </div>
          <div>
            <label className="glass-label">
              {t.acceptInvite.confirmPassword}
            </label>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className="glass-input w-full"
              placeholder={t.acceptInvite.reenterPassword}
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="glass-btn-primary w-full"
          >
            {submitting ? t.acceptInvite.creatingAccount : t.acceptInvite.createAndLogin}
          </button>
        </form>

        <p className="mt-4 text-center text-xs text-gray-500">
          {t.acceptInvite.haveAccount}{" "}
          <a href="/login" className="text-purple-400 hover:text-purple-300">
            {t.acceptInvite.logIn}
          </a>
        </p>
      </div>
    </div>
  );
}
