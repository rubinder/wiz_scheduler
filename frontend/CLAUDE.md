# Frontend conventions

Loaded only when working under `frontend/`. Project-wide rules live in the
root `CLAUDE.md`.

- **Use logical, not physical, direction utilities in Tailwind** — `text-start`
  not `text-left`, `ms-2` not `ml-2`, `border-s` not `border-l`, `start-0` not
  `left-0`, `gap-*` (or `space-x-* rtl:space-x-reverse`) not bare `space-x-*`.
  `ar` and `ur` are RTL and `LanguageContext` sets `document.dir`, so
  a physical utility looks right in 17 locales and wrong in 2.
  `frontend/src/utils/logicalDirection.test.ts` enforces this.
