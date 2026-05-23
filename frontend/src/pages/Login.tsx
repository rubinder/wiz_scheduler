import React, { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { ApiError } from "../api/client";
import { googleAuth, googleLink } from "../api/googleAuth";
import LanguageSelector from "../components/shared/LanguageSelector";
import { useAuth } from "../hooks/useAuth";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { text, bg, border, action } from "../theme";

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
    <div className="min-h-screen flex items-center justify-center">
      <div className="glass-card p-8 w-full max-w-md">
        <div className="flex justify-end mb-4">
          <LanguageSelector />
        </div>
        <h1 className={`text-2xl font-bold text-center mb-6 ${text.heading} flex items-center justify-center gap-2`}>
          {t.login.title} <img src="/favicon.svg" alt="" className="w-8 h-8 inline" />
        </h1>
        {error && (
          <div className="glass-alert-error mb-4">
            {error}
          </div>
        )}

        {/* Ownership group selection */}
        {ownershipGroups && !googleLinkState && (
          <div className="mb-4">
            <div className={`p-4 ${bg.section} rounded-xl border ${border.default}`}>
              <p className={`text-sm font-medium ${text.secondary} mb-3`}>
                {t.login.multiOrgPrompt}
              </p>
              <div className="space-y-2">
                {ownershipGroups.map((group) => (
                  <button
                    key={group.id}
                    onClick={() => handleSelectGroup(group.id)}
                    disabled={loading}
                    className={`w-full text-left px-4 py-3 ${bg.section} border ${border.default} rounded-lg hover:border-accent/40 ${bg.interactiveHover} transition-colors disabled:opacity-50`}
                  >
                    <span className={`text-sm font-medium ${text.secondary}`}>
                      {group.name}
                    </span>
                  </button>
                ))}
              </div>
              <button
                onClick={() => setOwnershipGroups(null)}
                className={`mt-3 text-xs ${text.muted} hover:${text.muted}`}
              >
                {t.login.backToLogin}
              </button>
            </div>
          </div>
        )}

        {/* Google account linking dialog */}
        {googleLinkState && (
          <div className="space-y-4">
            <div className={`p-4 ${bg.section} rounded-xl border ${border.default}`}>
              <p className={`text-sm ${text.secondary}`}>
                {t.login.googleLinkPrompt.replace("{email}", googleLinkState.email)}
              </p>
            </div>
            <form onSubmit={handleGoogleLink} className="space-y-4">
              <div>
                <label className="glass-label">{t.common.email}</label>
                <input
                  type="email"
                  value={googleLinkState.email}
                  disabled
                  className={`glass-input w-full ${bg.sectionSubtle} ${text.muted}`}
                />
              </div>
              <div>
                <label className="glass-label">{t.common.password}</label>
                <input
                  type="password"
                  required
                  value={linkPassword}
                  onChange={(e) => setLinkPassword(e.target.value)}
                  className="glass-input w-full"
                  placeholder={t.login.enterPasswordToLink}
                />
              </div>
              <button type="submit" disabled={loading} className="glass-btn-primary w-full">
                {loading ? t.login.linking : t.login.linkAndSignIn}
              </button>
              <button
                type="button"
                onClick={() => { setGoogleLinkState(null); setLinkPassword(""); }}
                className="glass-btn-secondary w-full"
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
                <div className="flex items-baseline justify-between">
                  <label className="glass-label">
                    {t.common.password}
                  </label>
                  <Link
                    to="/forgot-password"
                    className={`text-xs ${action.link}`}
                  >
                    {t.login.forgotPassword}
                  </Link>
                </div>
                <input
                  type="password"
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="glass-input w-full"
                />
              </div>
              <button
                type="submit"
                disabled={loading}
                className="glass-btn-primary w-full"
              >
                {loading ? t.login.signingIn : t.login.signIn}
              </button>
            </form>
            <div className="flex items-center gap-3 my-4">
              <div className={`flex-1 border-t ${border.default}`} />
              <span className={`text-xs ${text.muted}`}>{t.login.orContinueWith}</span>
              <div className={`flex-1 border-t ${border.default}`} />
            </div>
            <div ref={googleBtnRef} className="w-full flex justify-center" />
            <p className={`mt-4 text-center text-sm ${text.muted}`}>
              {t.login.noAccount}{" "}
              <Link to="/register" className={action.link}>
                {t.login.register}
              </Link>
            </p>
            <div className={`mt-6 p-4 ${bg.section} rounded-xl border ${border.default}`}>
              <p className={`text-xs font-semibold ${text.muted} uppercase tracking-wide mb-2`}>
                {t.login.demoCredentials}
              </p>
              <button
                type="button"
                onClick={() => {
                  setEmail("abc@example.com");
                  setPassword("example");
                }}
                className={`w-full text-left text-sm ${text.secondary} ${bg.subtleHover} rounded p-2 transition-colors`}
              >
                <span className="font-medium">{t.login.manager}:</span> abc@example.com / example
              </button>
              <button
                type="button"
                onClick={() => {
                  setEmail("employee1@example.com");
                  setPassword("example");
                }}
                className={`w-full text-left text-sm ${text.secondary} ${bg.subtleHover} rounded p-2 transition-colors`}
              >
                <span className="font-medium">{t.login.employeeLabel}:</span> employee1@example.com / example
              </button>
            </div>
          </>
        )}
        <div className={`mt-4 pt-4 border-t ${border.default} text-center text-xs ${text.muted} space-x-2`}>
          <Link to="/privacy-policy" className={action.link}>
            {t.gdpr.privacyPolicy}
          </Link>
          <span>|</span>
          <Link to="/terms" className={action.link}>
            {t.gdpr.termsOfService}
          </Link>
          <span>|</span>
          <Link to="/dpa" className={action.link}>
            {t.gdpr.dpa}
          </Link>
        </div>
      </div>
    </div>
  );
}
