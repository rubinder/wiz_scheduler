import { useLanguage } from "../../i18n/LanguageContext";
import { LANGUAGES } from "../../i18n/types";

export default function LanguageSelector() {
  const { lang, setLang } = useLanguage();

  return (
    <select
      value={lang}
      onChange={(e) => setLang(e.target.value as typeof lang)}
      className="glass-input-sm text-xs"
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
