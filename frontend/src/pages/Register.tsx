import React, { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import AuthLayout from "../components/marketing/AuthLayout";
import { useAuth } from "../hooks/useAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { marketing as m } from "../theme";

// `window.google` is already declared in Login.tsx with a permissive shape.
// No re-declaration here — we cast the GIS callback inline below.

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const { t } = useLanguage();
  useDocumentTitle("Register");

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Google Sign-Up: when set, the user has authenticated with Google and
  // we'll register without a password. Email/full_name are auto-filled from
  // the verified Google account and locked.
  const [googleIdToken, setGoogleIdToken] = useState<string | null>(null);
  const googleBtnRef = useRef<HTMLDivElement>(null);

  // Initialize Google Sign-In button once the GIS SDK is available and the
  // user hasn't already chosen the Google path.
  useEffect(() => {
    if (googleIdToken) return; // already signed in via Google
    const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID;
    if (!clientId || !window.google?.accounts?.id || !googleBtnRef.current) return;
    window.google.accounts.id.initialize({
      client_id: clientId,
      callback: (resp: { credential: string }) => {
        // resp.credential is the Google id_token. Decode the JWT payload
        // (no signature verification — backend does that on register).
        try {
          const payload = JSON.parse(atob(resp.credential.split(".")[1]));
          setEmail(payload.email || "");
          setFullName(payload.name || "");
          setGoogleIdToken(resp.credential);
          setError("");
        } catch {
          setError("Could not parse Google response. Please try again.");
        }
      },
    });
    window.google.accounts.id.renderButton(googleBtnRef.current, {
      theme: "outline",
      size: "large",
      text: "signup_with",
      shape: "rectangular",
      width: 360,
    });
  }, [googleIdToken]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      await register({
        email,
        password: googleIdToken ? undefined : password,
        googleIdToken: googleIdToken || undefined,
        fullName,
        companyName,
        privacyAccepted,
        termsAccepted,
      });
      navigate("/manager/dashboard");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout title={t.register.title}>
        {error && (
          <div className={`${m.alert.error} mb-4`}>
            {error}
          </div>
        )}
        {!googleIdToken && (
          <>
            <div ref={googleBtnRef} className="flex justify-center mb-4" />
            <div className="flex items-center gap-3 mb-4">
              <div className={`flex-1 border-t ${m.rule.line}`} />
              <span className={`text-xs ${m.text.muted}`}>{t.login.orContinueWith}</span>
              <div className={`flex-1 border-t ${m.rule.line}`} />
            </div>
          </>
        )}
        {googleIdToken && (
          <div className="mb-4 px-3 py-2 rounded-lg bg-emerald-50 border border-emerald-200 text-sm text-emerald-800 flex items-center justify-between">
            <span>Signed in with Google as {email}</span>
            <button
              type="button"
              onClick={() => {
                setGoogleIdToken(null);
                setEmail("");
                setFullName("");
              }}
              className="text-xs underline"
            >
              Use email + password instead
            </button>
          </div>
        )}
        <form onSubmit={handleSubmit} className="space-y-4">
          <div>
            <label className={m.label}>
              {t.register.fullName}
            </label>
            <input
              type="text"
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              readOnly={!!googleIdToken}
              className={`${m.input} ${googleIdToken ? "opacity-70 cursor-not-allowed" : ""}`}
            />
          </div>
          <div>
            <label className={m.label}>
              {t.common.email}
            </label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              readOnly={!!googleIdToken}
              className={`${m.input} ${googleIdToken ? "opacity-70 cursor-not-allowed" : ""}`}
            />
          </div>
          {!googleIdToken && (
            <div>
              <label className={m.label}>
                {t.common.password}
              </label>
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className={m.input}
              />
            </div>
          )}
          <div>
            <label className={m.label}>
              {t.register.companyName}
            </label>
            <input
              type="text"
              required
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              className={m.input}
            />
          </div>
          <div className="space-y-2">
            <label className={`flex items-start gap-2 text-sm ${m.text.body} cursor-pointer`}>
              <input
                type="checkbox"
                checked={privacyAccepted}
                onChange={(e) => setPrivacyAccepted(e.target.checked)}
                className="mt-1 accent-ink"
              />
              <span>
                {t.gdpr.acceptPrivacy}{" "}
                <a
                  href="/privacy-policy"
                  target="_blank"
                  rel="noopener noreferrer"
                  className={m.btn.link}
                >
                  {t.gdpr.privacyPolicy}
                </a>
              </span>
            </label>
            <label className={`flex items-start gap-2 text-sm ${m.text.body} cursor-pointer`}>
              <input
                type="checkbox"
                checked={termsAccepted}
                onChange={(e) => setTermsAccepted(e.target.checked)}
                className="mt-1 accent-ink"
              />
              <span>
                {t.gdpr.acceptTerms}{" "}
                <a
                  href="/terms"
                  target="_blank"
                  rel="noopener noreferrer"
                  className={m.btn.link}
                >
                  {t.gdpr.termsOfService}
                </a>
              </span>
            </label>
          </div>

          <button
            type="submit"
            disabled={loading || !privacyAccepted || !termsAccepted}
            className={`${m.btn.primary} w-full`}
          >
            {loading ? t.register.creatingAccount : t.register.createFreeAccount}
          </button>
        </form>
        <p className={`mt-3 text-center text-xs ${m.text.muted}`}>
          {t.register.googleLinkNote}
        </p>
        <p className={`mt-4 text-center text-sm ${m.text.muted}`}>
          {t.register.haveAccount}{" "}
          <Link to="/login" className={m.btn.link}>
            {t.register.signIn}
          </Link>
        </p>
    </AuthLayout>
  );
}
