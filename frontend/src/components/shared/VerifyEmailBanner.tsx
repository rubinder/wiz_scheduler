import { useState } from "react";

import { resendVerification } from "../../api/auth";
import { useAuth } from "../../hooks/useAuth";
import { useLanguage } from "../../i18n/LanguageContext";
import { marketing as m } from "../../theme";

/**
 * Shown above the generation controls while the signed-in user's address is
 * unproven. Renders nothing once email_verified is true.
 *
 * Sits next to PlanBanner but is deliberately a separate component: that one
 * explains what costs money, this one explains a free step. Merging them
 * would put an upgrade CTA next to a problem upgrading doesn't solve.
 */
export default function VerifyEmailBanner() {
  const { user } = useAuth();
  const { t } = useLanguage();
  const [sending, setSending] = useState(false);
  const [sent, setSent] = useState(false);

  if (!user || user.email_verified) return null;

  const handleResend = async () => {
    setSending(true);
    try {
      await resendVerification(user.email);
    } catch {
      // The endpoint is deliberately silent about outcomes, so there is no
      // failure worth reporting differently from success — showing the same
      // neutral confirmation either way is the honest rendering.
    } finally {
      setSending(false);
      setSent(true);
    }
  };

  return (
    <div className={`${m.alert.info} mb-4 flex flex-wrap items-center gap-3`}>
      <div className="flex-1 min-w-[16rem]">
        <p className="font-semibold">{t.verifyEmail.bannerTitle}</p>
        <p>{t.verifyEmail.bannerBody.replace("{email}", user.email)}</p>
        {sent && <p className="mt-1 opacity-70">{t.verifyEmail.resent}</p>}
      </div>
      <button
        type="button"
        onClick={handleResend}
        disabled={sending || sent}
        className={`${m.btn.secondary} text-sm`}
      >
        {sending ? t.verifyEmail.resending : t.verifyEmail.resend}
      </button>
    </div>
  );
}
