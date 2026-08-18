import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { acceptManagerInvite, getManagerInviteInfo } from "../api/managerInvites";
import type { ManagerInviteInfo } from "../types";
import AuthLayout from "../components/marketing/AuthLayout";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { text, border, bg } from "../theme";
import { marketing as m } from "../theme";

export default function AcceptManagerInvite() {
  useDocumentTitle("Accept Manager Invite");
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");
  const navigate = useNavigate();
  const { t } = useLanguage();

  const [info, setInfo] = useState<ManagerInviteInfo | null>(null);
  const [fullName, setFullName] = useState("");
  const [companyId, setCompanyId] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!token) {
      setError(t.acceptManagerInvite.noToken);
      setLoading(false);
      return;
    }
    getManagerInviteInfo(token)
      .then((data) => {
        if (data.expired) {
          setError(t.acceptManagerInvite.invalidOrExpired);
          setInfo(null);
        } else {
          setInfo(data);
          if (data.companies.length > 0) {
            setCompanyId(data.companies[0].id);
          }
        }
      })
      .catch((err) => setError(err instanceof Error ? err.message : t.acceptManagerInvite.invalidOrExpired))
      .finally(() => setLoading(false));
  }, [token]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");

    if (!fullName.trim()) {
      setError(t.acceptManagerInvite.fullNameRequired);
      return;
    }
    if (!companyId) {
      setError(t.acceptManagerInvite.companyRequired);
      return;
    }
    if (password.length < 6) {
      setError(t.acceptManagerInvite.passwordMin);
      return;
    }
    if (password !== confirm) {
      setError(t.acceptManagerInvite.passwordMismatch);
      return;
    }
    if (!token) return;

    setSubmitting(true);
    try {
      const result = await acceptManagerInvite({
        token,
        company_id: companyId,
        full_name: fullName.trim(),
        password,
      });
      localStorage.setItem("token", result.access_token);
      navigate("/manager/dashboard", { replace: true });
      // Force a full reload so useAuth picks up the new token
      window.location.reload();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : t.acceptManagerInvite.failedCreate);
    } finally {
      setSubmitting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center">
        <div className={m.text.muted}>{t.acceptManagerInvite.loadingInvite}</div>
      </div>
    );
  }

  if (!info) {
    return (
      <AuthLayout title={t.acceptManagerInvite.invalidInvite}>
          <p className={m.text.muted}>{error || t.acceptManagerInvite.invalidOrExpired}</p>
          <a
            href="/login"
            className={`mt-4 inline-block ${m.btn.link} text-sm`}
          >
            {t.acceptManagerInvite.goToLogin}
          </a>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title={t.acceptManagerInvite.title}>
        <p className={`text-sm ${m.text.muted} mb-6`}>
          {t.acceptManagerInvite.welcomeTo} <strong className={text.secondary}>{info.group_name}</strong>.
          <br />
          {t.acceptManagerInvite.choosePassword}
        </p>

        {error && (
          <div className={`${m.alert.error} mb-4`}>
            {error}
          </div>
        )}

        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className={m.label}>
              {t.acceptManagerInvite.emailLabel}
            </label>
            <input
              type="email"
              value={info.email}
              disabled
              className={`w-full border ${border.subtle} rounded px-3 py-2 text-sm ${bg.sectionSubtle} ${m.text.muted}`}
            />
          </div>
          <div>
            <label className={m.label}>
              {t.acceptManagerInvite.fullNameLabel}
            </label>
            <input
              type="text"
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              className={m.input}
              autoFocus
              required
            />
          </div>
          <div>
            <label className={m.label}>
              {t.acceptManagerInvite.companyLabel}
            </label>
            <select
              value={companyId}
              onChange={(e) => setCompanyId(e.target.value)}
              className={`${m.input} appearance-none`}
              required
            >
              {info.companies.map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
            </select>
          </div>
          <div>
            <label className={m.label}>
              {t.acceptManagerInvite.passwordLabel}
            </label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className={m.input}
              placeholder={t.acceptManagerInvite.atLeast6}
              required
            />
          </div>
          <div>
            <label className={m.label}>
              {t.acceptManagerInvite.confirmPasswordLabel}
            </label>
            <input
              type="password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
              className={m.input}
              placeholder={t.acceptManagerInvite.reenterPassword}
              required
            />
          </div>
          <button
            type="submit"
            disabled={submitting}
            className={`${m.btn.primary} w-full`}
          >
            {submitting ? t.acceptManagerInvite.submitting : t.acceptManagerInvite.submitButton}
          </button>
        </form>

        <p className={`mt-4 text-center text-xs ${m.text.muted}`}>
          {t.acceptManagerInvite.haveAccount}{" "}
          <a href="/login" className={m.btn.link}>
            {t.acceptManagerInvite.logIn}
          </a>
        </p>
    </AuthLayout>
  );
}
