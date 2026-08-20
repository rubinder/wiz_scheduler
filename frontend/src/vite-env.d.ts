/// <reference types="vite/client" />

// Declaration merging with vite/client's ImportMetaEnv. Keeps the build-time
// variables explicit so a typo surfaces at compile time rather than as an
// undefined at runtime.
interface ImportMetaEnv {
  readonly VITE_GOOGLE_CLIENT_ID?: string;
  /** Password for the public demo accounts; supplied by CI from Secrets Manager. */
  readonly VITE_DEMO_PASSWORD?: string;
}
