export type Language =
  | "en" | "zh" | "hi" | "ar" | "fr"
  | "es" | "pt" | "bn" | "ru" | "ur"
  | "id" | "de" | "pcm" | "te" | "tr"
  | "ta" | "vi" | "ja" | "mr";

export interface LanguageOption {
  code: Language;
  label: string;
  nativeLabel: string;
  dir: "ltr" | "rtl";
}

// Sorted per project requirement
export const LANGUAGES: LanguageOption[] = [
  { code: "en", label: "English", nativeLabel: "English", dir: "ltr" },
  { code: "zh", label: "Chinese", nativeLabel: "\u4e2d\u6587", dir: "ltr" },
  { code: "hi", label: "Hindi", nativeLabel: "\u0939\u093f\u0928\u094d\u0926\u0940", dir: "ltr" },
  { code: "es", label: "Spanish", nativeLabel: "Espa\u00f1ol", dir: "ltr" },
  { code: "fr", label: "French", nativeLabel: "Fran\u00e7ais", dir: "ltr" },
  { code: "ar", label: "Arabic", nativeLabel: "\u0627\u0644\u0639\u0631\u0628\u064a\u0629", dir: "rtl" },
  { code: "bn", label: "Bengali", nativeLabel: "\u09ac\u09be\u0982\u09b2\u09be", dir: "ltr" },
  { code: "ru", label: "Russian", nativeLabel: "\u0420\u0443\u0441\u0441\u043a\u0438\u0439", dir: "ltr" },
  { code: "pt", label: "Portuguese", nativeLabel: "Portugu\u00eas", dir: "ltr" },
  { code: "ur", label: "Urdu", nativeLabel: "\u0627\u0631\u062f\u0648", dir: "rtl" },
  { code: "id", label: "Indonesian", nativeLabel: "Bahasa Indonesia", dir: "ltr" },
  { code: "de", label: "German", nativeLabel: "Deutsch", dir: "ltr" },
  { code: "ja", label: "Japanese", nativeLabel: "\u65e5\u672c\u8a9e", dir: "ltr" },
  { code: "pcm", label: "Nigerian Pidgin", nativeLabel: "Naij\u00e1 P\u00edjin", dir: "ltr" },
  { code: "mr", label: "Marathi", nativeLabel: "\u092e\u0930\u093e\u0920\u0940", dir: "ltr" },
  { code: "te", label: "Telugu", nativeLabel: "\u0c24\u0c46\u0c32\u0c41\u0c17\u0c41", dir: "ltr" },
  { code: "tr", label: "Turkish", nativeLabel: "T\u00fcrk\u00e7e", dir: "ltr" },
  { code: "ta", label: "Tamil", nativeLabel: "\u0ba4\u0bae\u0bbf\u0bb4\u0bcd", dir: "ltr" },
  { code: "vi", label: "Vietnamese", nativeLabel: "Ti\u1ebfng Vi\u1ec7t", dir: "ltr" },
];

export type TranslationKeys = typeof import("./en").default;
