import { useLanguage } from "../../i18n/LanguageContext";
import { LANGUAGES } from "../../i18n/types";
import { marketing as m } from "../../theme";

interface Props {
  /**
   * "glass" (default) keeps the translucent app-wide look used across the
   * ~30 logged-in pages. "rota" is squared-off, newsprint-and-ink styling
   * for the marketing surface (nav, auth pages) — never used in the app.
   */
  variant?: "glass" | "rota";
}

const ROTA_CLASSES =
  `bg-transparent ${m.text.meta} hover:text-ink border ${m.rule.line} px-2 py-1 ` +
  "focus:outline-none focus:border-ink focus-visible:outline focus-visible:outline-2 " +
  "focus-visible:outline-offset-1 focus-visible:outline-ink transition-colors";

export default function LanguageSelector({ variant = "glass" }: Props) {
  const { lang, setLang } = useLanguage();

  return (
    <select
      value={lang}
      onChange={(e) => setLang(e.target.value as typeof lang)}
      className={variant === "rota" ? ROTA_CLASSES : "glass-input-sm text-xs"}
      aria-label="Language"
    >
      {LANGUAGES.map((l) => (
        <option key={l.code} value={l.code}>
          {l.nativeLabel}
        </option>
      ))}
    </select>
  );
}
