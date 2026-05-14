import React, { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { createCheckoutSession } from "../api/auth";
import { useAuth } from "../hooks/useAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { text, border, action } from "../theme";

// `window.google` is already declared in Login.tsx with a permissive shape.
// No re-declaration here — we cast the GIS callback inline below.

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const { t } = useLanguage();
  useDocumentTitle("Register");
  const [searchParams, setSearchParams] = useSearchParams();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [companyName, setCompanyName] = useState("");
  const [privacyAccepted, setPrivacyAccepted] = useState(false);
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [billingLoading, setBillingLoading] = useState(false);

  // Google Sign-Up: when set, the user has authenticated with Google and
  // we'll register without a password. Email/full_name are auto-filled from
  // the verified Google account and locked.
  const [googleIdToken, setGoogleIdToken] = useState<string | null>(null);
  const googleBtnRef = useRef<HTMLDivElement>(null);

  // Stripe session from redirect
  const [stripeSessionId, setStripeSessionId] = useState<string | null>(null);

  // On mount, check if returning from Stripe Checkout
  useEffect(() => {
    const sessionId = searchParams.get("session_id");
    if (sessionId) {
      setStripeSessionId(sessionId);
      // Restore form data from sessionStorage
      setEmail(sessionStorage.getItem("reg_email") || "");
      setPassword(sessionStorage.getItem("reg_password") || "");
      setFullName(sessionStorage.getItem("reg_fullName") || "");
      setCompanyName(sessionStorage.getItem("reg_companyName") || "");
      setPrivacyAccepted(sessionStorage.getItem("reg_privacy") === "true");
      setTermsAccepted(sessionStorage.getItem("reg_terms") === "true");
      const restoredGoogle = sessionStorage.getItem("reg_googleIdToken");
      if (restoredGoogle) setGoogleIdToken(restoredGoogle);
      // Clean up URL
      searchParams.delete("session_id");
      setSearchParams(searchParams, { replace: true });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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

  const billingComplete = !!stripeSessionId;

  const handleSetupBilling = async () => {
    if (!email) {
      setError(t.register.emailRequiredForBilling);
      return;
    }
    setError("");
    setBillingLoading(true);

    // Save form state so we can restore it after Stripe redirect
    sessionStorage.setItem("reg_email", email);
    sessionStorage.setItem("reg_password", password);
    sessionStorage.setItem("reg_fullName", fullName);
    sessionStorage.setItem("reg_companyName", companyName);
    sessionStorage.setItem("reg_privacy", String(privacyAccepted));
    sessionStorage.setItem("reg_terms", String(termsAccepted));
    if (googleIdToken) {
      sessionStorage.setItem("reg_googleIdToken", googleIdToken);
    }

    try {
      const { url } = await createCheckoutSession(email);
      window.location.href = url;
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to start billing setup");
      setBillingLoading(false);
    }
  };

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
        stripeSessionId: stripeSessionId || undefined,
      });
      // Clean up sessionStorage
      sessionStorage.removeItem("reg_email");
      sessionStorage.removeItem("reg_password");
      sessionStorage.removeItem("reg_fullName");
      sessionStorage.removeItem("reg_companyName");
      sessionStorage.removeItem("reg_privacy");
      sessionStorage.removeItem("reg_terms");
      sessionStorage.removeItem("reg_googleIdToken");
      navigate("/manager/dashboard");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center">
      <div className="glass-card p-8 w-full max-w-md">
        <h1 className={`text-2xl font-bold text-center mb-6 ${text.heading}`}>
          {t.register.title}
        </h1>
        {error && (
          <div className="glass-alert-error mb-4">
            {error}
          </div>
        )}
        {!googleIdToken && (
          <>
            <div ref={googleBtnRef} className="flex justify-center mb-4" />
            <div className="flex items-center gap-3 mb-4">
              <div className={`flex-1 border-t ${border.default}`} />
              <span className={`text-xs ${text.muted}`}>{t.login.orContinueWith}</span>
              <div className={`flex-1 border-t ${border.default}`} />
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
            <label className="glass-label">
              {t.register.fullName}
            </label>
            <input
              type="text"
              required
              value={fullName}
              onChange={(e) => setFullName(e.target.value)}
              readOnly={!!googleIdToken}
              className={`glass-input w-full ${googleIdToken ? "opacity-70 cursor-not-allowed" : ""}`}
            />
          </div>
          <div>
            <label className="glass-label">
              {t.common.email}
            </label>
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              readOnly={!!googleIdToken}
              className={`glass-input w-full ${googleIdToken ? "opacity-70 cursor-not-allowed" : ""}`}
            />
          </div>
          {!googleIdToken && (
            <div>
              <label className="glass-label">
                {t.common.password}
              </label>
              <input
                type="password"
                required
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="glass-input w-full"
              />
            </div>
          )}
          <div>
            <label className="glass-label">
              {t.register.companyName}
            </label>
            <input
              type="text"
              required
              value={companyName}
              onChange={(e) => setCompanyName(e.target.value)}
              className="glass-input w-full"
            />
          </div>
          <div className="space-y-2">
            <label className={`flex items-start gap-2 text-sm ${text.secondary} cursor-pointer`}>
              <input
                type="checkbox"
                checked={privacyAccepted}
                onChange={(e) => setPrivacyAccepted(e.target.checked)}
                className="mt-1 accent-[#9e6934]"
              />
              <span>
                {t.gdpr.acceptPrivacy}{" "}
                <a
                  href="/privacy-policy"
                  target="_blank"
                  rel="noopener noreferrer"
                  className={action.link}
                >
                  {t.gdpr.privacyPolicy}
                </a>
              </span>
            </label>
            <label className={`flex items-start gap-2 text-sm ${text.secondary} cursor-pointer`}>
              <input
                type="checkbox"
                checked={termsAccepted}
                onChange={(e) => setTermsAccepted(e.target.checked)}
                className="mt-1 accent-[#9e6934]"
              />
              <span>
                {t.gdpr.acceptTerms}{" "}
                <a
                  href="/terms"
                  target="_blank"
                  rel="noopener noreferrer"
                  className={action.link}
                >
                  {t.gdpr.termsOfService}
                </a>
              </span>
            </label>
          </div>

          {/* Billing section */}
          <div className={`border ${border.default} rounded-lg p-4 space-y-3`}>
            <div className="flex items-center justify-between">
              <span className={`text-sm font-medium ${text.secondary}`}>
                {t.register.billing}
              </span>
              {billingComplete ? (
                <span className="text-xs font-medium text-emerald-600 flex items-center gap-1">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  {t.register.billingComplete}
                </span>
              ) : (
                <span className={`text-xs ${text.muted}`}>
                  {t.register.billingRequired}
                </span>
              )}
            </div>
            {!billingComplete && (
              <button
                type="button"
                onClick={handleSetupBilling}
                disabled={billingLoading || !email}
                className="glass-btn-secondary w-full text-sm"
              >
                {billingLoading ? t.register.redirectingToStripe : t.register.setupBilling}
              </button>
            )}
          </div>

          <button
            type="submit"
            disabled={loading || !privacyAccepted || !termsAccepted || !billingComplete}
            className="glass-btn-primary w-full"
          >
            {loading ? t.register.creatingAccount : t.register.registerBtn}
          </button>
        </form>
        <p className={`mt-3 text-center text-xs ${text.muted}`}>
          {t.register.googleLinkNote}
        </p>
        <p className={`mt-4 text-center text-sm ${text.muted}`}>
          {t.register.haveAccount}{" "}
          <Link to="/login" className={action.link}>
            {t.register.signIn}
          </Link>
        </p>
      </div>
    </div>
  );
}
