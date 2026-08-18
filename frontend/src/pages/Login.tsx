import React, { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { googleAuth, googleLink } from "../api/googleAuth";
import AuthLayout from "../components/marketing/AuthLayout";
import { useAuth } from "../hooks/useAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { marketing as m } from "../theme";

declare global {
  interface Window {
    google?: {
      accounts: {
        id: {
          initialize: (config: Record<string, unknown>) => void;
          renderButton: (el: HTMLElement, config: Record<string, unknown>) => void;
        };
      };
    };
  }
}

interface OwnershipGroupOption {
  id: string;
  name: string;
}

export default function Login() {
  const { login } = useAuth();
  const { t } = useLanguage();
  useDocumentTitle("Sign In");
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  // Ownership group selection state
  const [ownershipGroups, setOwnershipGroups] = useState<
    OwnershipGroupOption[] | null
  >(null);

  // Google link flow state
  const [googleLinkState, setGoogleLinkState] = useState<{
    idToken: string;
    email: string;
    googleName: string;
  } | null>(null);
  const [linkPassword, setLinkPassword] = useState("");

  const googleBtnRef = useRef<HTMLDivElement>(null);

  const handleGoogleCallback = async (response: { credential: string }) => {
    setError("");
    setLoading(true);
    try {
      const result = await googleAuth(response.credential);
      if (result.access_token) {
        localStorage.setItem("token", result.access_token);
        window.location.href = "/manager/dashboard";
      } else if (result.link_required) {
        setGoogleLinkState({
          idToken: response.credential,
          email: result.email || "",
          googleName: result.google_name || "",
        });
      }
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Google sign-in failed");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    const clientId = import.meta.env.VITE_GOOGLE_CLIENT_ID;
    if (!clientId || !window.google?.accounts?.id) return;

    window.google.accounts.id.initialize({
      client_id: clientId,
      callback: handleGoogleCallback,
    });

    if (googleBtnRef.current) {
      window.google.accounts.id.renderButton(googleBtnRef.current, {
        theme: "filled_black",
        size: "large",
        width: "100%",
        text: "signin_with",
      });
    }
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError("");
    setLoading(true);
    setOwnershipGroups(null);
    try {
      await login(email, password);
      navigate("/manager/dashboard");
    } catch (err: unknown) {
      if (
        err instanceof ApiError &&
        err.status === 409 &&
        typeof err.data === "object" &&
        err.data !== null &&
        (err.data as Record<string, unknown>).code ===
          "multiple_ownership_groups"
      ) {
        const groups = (err.data as Record<string, unknown>)
          .groups as OwnershipGroupOption[];
        setOwnershipGroups(groups);
      } else {
        setError(err instanceof Error ? err.message : "Login failed");
      }
    } finally {
      setLoading(false);
    }
  };

  const handleSelectGroup = async (groupId: string) => {
    setError("");
    setLoading(true);
    try {
      await login(email, password, groupId);
      navigate("/manager/dashboard");
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Login failed");
    } finally {
      setLoading(false);
    }
  };

  const handleGoogleLink = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!googleLinkState) return;
    setError("");
    setLoading(true);
    try {
      const result = await googleLink(
        googleLinkState.idToken,
        googleLinkState.email,
        linkPassword,
      );
      localStorage.setItem("token", result.access_token);
      window.location.href = "/manager/dashboard";
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Failed to link account");
    } finally {
      setLoading(false);
    }
  };

  return (
    <AuthLayout title={t.login.title}>
        {error && (
          <div className={`${m.alert.error} mb-4`}>
            {error}
          </div>
        )}

        {/* Ownership group selection */}
        {ownershipGroups && !googleLinkState && (
          <div className="mb-4">
            <div className={`p-4 ${m.surface} border ${m.rule.line}`}>
              <p className={`text-sm font-medium ${m.text.body} mb-3`}>
                {t.login.multiOrgPrompt}
              </p>
              <div className="space-y-2">
                {ownershipGroups.map((group) => (
                  <button
                    key={group.id}
                    onClick={() => handleSelectGroup(group.id)}
                    disabled={loading}
                    className={`w-full text-start px-4 py-3 ${m.surface} border ${m.rule.line} hover:border-ink hover:bg-ink/[0.04] transition-colors disabled:opacity-50`}
                  >
                    <span className={`text-sm font-medium ${m.text.body}`}>
                      {group.name}
                    </span>
                  </button>
                ))}
              </div>
              <button
                onClick={() => setOwnershipGroups(null)}
                className={`mt-3 text-xs ${m.text.muted} hover:${m.text.muted}`}
              >
                {t.login.backToLogin}
              </button>
            </div>
          </div>
        )}

        {/* Google account linking dialog */}
        {googleLinkState && (
          <div className="space-y-4">
            <div className={`p-4 ${m.surface} border ${m.rule.line}`}>
              <p className={`text-sm ${m.text.body}`}>
                {t.login.googleLinkPrompt.replace("{email}", googleLinkState.email)}
              </p>
            </div>
            <form onSubmit={handleGoogleLink} className="space-y-4">
              <div>
                <label className={m.label}>{t.common.email}</label>
                <input
                  type="email"
                  value={googleLinkState.email}
                  disabled
                  className={`${m.input} bg-ink/[0.03] ${m.text.muted}`}
                />
              </div>
              <div>
                <label className={m.label}>{t.common.password}</label>
                <input
                  type="password"
                  required
                  value={linkPassword}
                  onChange={(e) => setLinkPassword(e.target.value)}
                  className={m.input}
                  placeholder={t.login.enterPasswordToLink}
                />
              </div>
              <button type="submit" disabled={loading} className={`${m.btn.primary} w-full`}>
                {loading ? t.login.linking : t.login.linkAndSignIn}
              </button>
              <button
                type="button"
                onClick={() => { setGoogleLinkState(null); setLinkPassword(""); }}
                className={`${m.btn.secondary} w-full`}
              >
                {t.login.backToLogin}
              </button>
            </form>
          </div>
        )}

        {/* Login form — hidden when choosing ownership group or linking Google */}
        {!ownershipGroups && !googleLinkState && (
          <>
            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className={m.label}>
                  {t.common.email}
                </label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className={m.input}
                />
              </div>
              <div>
                <div className="flex items-baseline justify-between">
                  <label className={m.label}>
                    {t.common.password}
                  </label>
                  <Link
                    to="/forgot-password"
                    className={`text-xs ${m.btn.link}`}
                  >
                    {t.login.forgotPassword}
                  </Link>
                </div>
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className={m.input}
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className={`${m.btn.primary} w-full`}
              >
                {loading ? t.login.signingIn : t.login.signIn}
              </button>
            </form>
            <div className="flex items-center gap-3 my-4">
              <div className={`flex-1 border-t ${m.rule.line}`} />
              <span className={`text-xs ${m.text.muted}`}>{t.login.orContinueWith}</span>
              <div className={`flex-1 border-t ${m.rule.line}`} />
            </div>
            <div ref={googleBtnRef} className="w-full flex justify-center" />
            <p className={`mt-4 text-center text-sm ${m.text.muted}`}>
              {t.login.noAccount}{" "}
              <Link to="/register" className={m.btn.link}>
                {t.login.register}
              </Link>
            </p>
            <div className={`mt-6 ${m.surface} border ${m.rule.line} p-4 font-data text-xs`}>
              <p className={`text-xs font-semibold ${m.text.muted} uppercase tracking-wide mb-2`}>
                {t.login.demoCredentials}
              </p>
              <button
                type="button"
                onClick={() => {
                  setEmail("abc@example.com");
                  setPassword("example");
                }}
                className={`w-full text-start text-sm ${m.text.body} hover:bg-ink/[0.04] rounded p-2 transition-colors`}
              >
                <span className="font-medium">{t.login.manager}:</span> abc@example.com / example
              </button>
              <button
                type="button"
                onClick={() => {
                  setEmail("employee1@example.com");
                  setPassword("example");
                }}
                className={`w-full text-start text-sm ${m.text.body} hover:bg-ink/[0.04] rounded p-2 transition-colors`}
              >
                <span className="font-medium">{t.login.employeeLabel}:</span> employee1@example.com / example
              </button>
            </div>
          </>
        )}
        <div
          className={`mt-4 pt-4 border-t ${m.rule.line} flex flex-wrap items-center justify-center gap-x-2 text-center text-xs ${m.text.muted}`}
        >
          <Link to="/privacy-policy" className={m.btn.link}>
            {t.gdpr.privacyPolicy}
          </Link>
          <span>|</span>
          <Link to="/terms" className={m.btn.link}>
            {t.gdpr.termsOfService}
          </Link>
          <span>|</span>
          <Link to="/dpa" className={m.btn.link}>
            {t.gdpr.dpa}
          </Link>
        </div>
    </AuthLayout>
  );
}
