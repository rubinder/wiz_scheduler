import React, { useEffect, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { createCheckoutSession } from "../api/auth";
import { useAuth } from "../hooks/useAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";

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
      // Clean up URL
      searchParams.delete("session_id");
      setSearchParams(searchParams, { replace: true });
    }
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

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
      await register(
        email,
        password,
        fullName,
        companyName,
        privacyAccepted,
        termsAccepted,
        stripeSessionId || undefined
      );
      // Clean up sessionStorage
      sessionStorage.removeItem("reg_email");
      sessionStorage.removeItem("reg_password");
      sessionStorage.removeItem("reg_fullName");
      sessionStorage.removeItem("reg_companyName");
      sessionStorage.removeItem("reg_privacy");
      sessionStorage.removeItem("reg_terms");
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
        <h1 className="text-2xl font-bold text-center mb-6 text-white">
          {t.register.title}
        </h1>
        {error && (
          <div className="glass-alert-error mb-4">
            {error}
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
              className="glass-input w-full"
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
              className="glass-input w-full"
            />
          </div>
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
            <label className="flex items-start gap-2 text-sm text-gray-300 cursor-pointer">
              <input
                type="checkbox"
                checked={privacyAccepted}
                onChange={(e) => setPrivacyAccepted(e.target.checked)}
                className="mt-1 accent-purple-500"
              />
              <span>
                {t.gdpr.acceptPrivacy}{" "}
                <a
                  href="/privacy-policy"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-purple-400 hover:text-purple-300"
                >
                  {t.gdpr.privacyPolicy}
                </a>
              </span>
            </label>
            <label className="flex items-start gap-2 text-sm text-gray-300 cursor-pointer">
              <input
                type="checkbox"
                checked={termsAccepted}
                onChange={(e) => setTermsAccepted(e.target.checked)}
                className="mt-1 accent-purple-500"
              />
              <span>
                {t.gdpr.acceptTerms}{" "}
                <a
                  href="/terms"
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-purple-400 hover:text-purple-300"
                >
                  {t.gdpr.termsOfService}
                </a>
              </span>
            </label>
          </div>

          {/* Billing section */}
          <div className="border border-white/10 rounded-lg p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-gray-300">
                {t.register.billing}
              </span>
              {billingComplete ? (
                <span className="text-xs font-medium text-green-400 flex items-center gap-1">
                  <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                  </svg>
                  {t.register.billingComplete}
                </span>
              ) : (
                <span className="text-xs text-gray-500">
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
        <p className="mt-3 text-center text-xs text-gray-500">
          {t.register.googleLinkNote}
        </p>
        <p className="mt-4 text-center text-sm text-gray-400">
          {t.register.haveAccount}{" "}
          <Link to="/login" className="text-purple-400 hover:text-purple-300">
            {t.register.signIn}
          </Link>
        </p>
      </div>
    </div>
  );
}
