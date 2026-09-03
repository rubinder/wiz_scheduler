import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { verifyEmail } from "../api/auth";
import AuthLayout from "../components/marketing/AuthLayout";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { useLanguage } from "../i18n/LanguageContext";
import { marketing as m } from "../theme";

type State = "verifying" | "ok" | "invalid";

/**
 * Landing page for the link in the confirmation email.
 *
 * Redeems the token on mount and stores the session it returns, so opening
 * the email on a second device signs that device in — the common "signed up
 * on the laptop, read the mail on the phone" path.
 */
export default function VerifyEmail() {
  useDocumentTitle("Confirm Email");
  const { t } = useLanguage();
  const [searchParams] = useSearchParams();
  const token = searchParams.get("token");

  const [state, setState] = useState<State>(token ? "verifying" : "invalid");
  // The token is single-use, so a double-invoke (React 18 StrictMode in dev)
  // would redeem it and then report the second call's 410 as a dead link.
  const attempted = useRef(false);

  useEffect(() => {
    if (!token || attempted.current) return;
    attempted.current = true;

    verifyEmail(token)
      .then((res) => {
        localStorage.setItem("token", res.access_token);
        setState("ok");
      })
      .catch(() => setState("invalid"));
  }, [token]);

  if (state === "verifying") {
    return (
      <AuthLayout title={t.verifyEmail.bannerTitle}>
        <p className={m.text.muted}>{t.verifyEmail.verifying}</p>
      </AuthLayout>
    );
  }

  if (state === "invalid") {
    return (
      <AuthLayout title={t.verifyEmail.invalidTitle}>
        <p className={m.text.muted}>{t.verifyEmail.invalidBody}</p>
        <Link to="/login" className={`mt-4 inline-block ${m.btn.link} text-sm`}>
          {t.verifyEmail.backToLogin}
        </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title={t.verifyEmail.successTitle}>
      <p className={`text-sm ${m.text.muted} mb-6`}>
        {t.verifyEmail.successBody}
      </p>
      {/* Full reload so AuthProvider re-fetches /auth/me and the banner,
          which keys off email_verified, disappears. */}
      <a href="/manager/dashboard" className={`${m.btn.primary} w-full`}>
        {t.verifyEmail.goToDashboard}
      </a>
    </AuthLayout>
  );
}
