import { createContext, useCallback, useContext, useEffect, useState } from "react";
import type { Language } from "./types";
import { LANGUAGES } from "./types";
import en from "./en";
import zh from "./zh";
import hi from "./hi";
import ar from "./ar";
import fr from "./fr";
import es from "./es";
import pt from "./pt";
import bn from "./bn";
import ru from "./ru";
import ur from "./ur";
import id from "./id";
import de from "./de";
import pcm from "./pcm";
import te from "./te";
import tr from "./tr";
import ta from "./ta";
import vi from "./vi";
import ja from "./ja";
import mr from "./mr";

// Widen literal string types from `as const` so all translations are compatible
type DeepStringify<T> = {
  [K in keyof T]: T[K] extends string ? string : DeepStringify<T[K]>;
};

type Translations = DeepStringify<typeof en>;

const translationMap: Record<Language, Translations> = {
  en, zh, hi, ar, fr,
  es, pt, bn, ru, ur,
  id, de, pcm, te, tr,
  ta, vi, ja, mr,
};

interface LanguageContextValue {
  lang: Language;
  setLang: (lang: Language) => void;
  t: Translations;
}

const LanguageContext = createContext<LanguageContextValue>({
  lang: "en",
  setLang: () => {},
  t: en,
});

function detectBrowserLang(): Language | undefined {
  for (const tag of navigator.languages ?? [navigator.language]) {
    const exact = tag.toLowerCase() as Language;
    if (translationMap[exact]) return exact;
    const prefix = exact.split("-")[0] as Language;
    if (translationMap[prefix]) return prefix;
  }
  return undefined;
}

function getInitialLang(): Language {
  const stored = localStorage.getItem("lang");
  if (stored && translationMap[stored as Language]) return stored as Language;
  return detectBrowserLang() ?? "en";
}

export function LanguageProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Language>(getInitialLang);

  const setLang = useCallback((l: Language) => {
    setLangState(l);
    localStorage.setItem("lang", l);
  }, []);

  // Update document lang and dir for SEO + RTL support
  useEffect(() => {
    const info = LANGUAGES.find((lo) => lo.code === lang);
    document.documentElement.lang = lang;
    document.documentElement.dir = info?.dir ?? "ltr";
  }, [lang]);

  const t = translationMap[lang];

  return (
    <LanguageContext.Provider value={{ lang, setLang, t }}>
      {children}
    </LanguageContext.Provider>
  );
}

export function useLanguage() {
  return useContext(LanguageContext);
}
