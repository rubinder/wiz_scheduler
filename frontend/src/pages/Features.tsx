import { Link } from "react-router-dom";
import { useLanguage } from "../i18n/LanguageContext";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import LanguageSelector from "../components/shared/LanguageSelector";
import { text, border } from "../theme";

const PAGE_SLUGS = [
  "dashboard",
  "company",
  "regions",
  "locations",
  "roles",
  "role-equivalents",
  "employees",
  "hour-restrictions",
  "day-blackouts",
  "employee-onboarding",
  "employee-association",
  "shift-templates",
  "schedule",
  "export-schedules",
  "data-privacy",
] as const;

type Slug = (typeof PAGE_SLUGS)[number];

export default function Features() {
  const { t } = useLanguage();
  useDocumentTitle("Manager Tour");

  return (
    <div className="min-h-screen">
      {/* Nav */}
      <nav
        className={`fixed top-0 inset-x-0 z-50 bg-white/50 backdrop-blur-2xl border-b ${border.default}`}
      >
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <Link to="/" className="flex items-center gap-3">
            <img src="/favicon.svg" alt="" className="w-8 h-8" />
            <span className={`text-xl font-bold ${text.primary} tracking-wide`}>
              Wiz Scheduler
            </span>
          </Link>
          <div className="flex items-center gap-4">
            <LanguageSelector />
            <Link
              to="/"
              className={`text-sm ${text.secondary} hover:${text.heading} transition-colors`}
            >
              {t.features.backToHome}
            </Link>
            <Link to="/register" className="glass-btn-primary text-sm">
              {t.register.registerBtn}
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="pt-32 pb-12 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <h1
            className={`text-4xl sm:text-5xl font-extrabold ${text.heading} leading-tight mb-6`}
          >
            {t.features.pageTitle}
          </h1>
          <p
            className={`text-lg ${text.muted} max-w-2xl mx-auto leading-relaxed`}
          >
            {t.features.pageIntro}
          </p>
        </div>
      </section>

      {/* Body: sticky TOC (desktop) + screen rows */}
      <section className="px-6 pb-20">
        <div className="max-w-7xl mx-auto lg:grid lg:grid-cols-[220px_1fr] lg:gap-10">
          {/* TOC */}
          <aside className="hidden lg:block">
            <div className="sticky top-24">
              <h2
                className={`text-xs font-semibold uppercase tracking-wider ${text.muted} mb-3`}
              >
                {t.features.tocTitle}
              </h2>
              <nav className="flex flex-col gap-1">
                {PAGE_SLUGS.map((slug) => (
                  <a
                    key={slug}
                    href={`#${slug}`}
                    className={`text-sm ${text.secondary} hover:${text.heading} transition-colors py-1`}
                  >
                    {t.features.pages[slug].title}
                  </a>
                ))}
              </nav>
            </div>
          </aside>

          {/* Rows */}
          <div className="flex flex-col gap-10">
            {PAGE_SLUGS.map((slug, idx) => {
              const reverse = idx % 2 === 1;
              return (
                <article
                  id={slug}
                  key={slug}
                  className={`glass-card p-6 lg:p-8 scroll-mt-24 grid grid-cols-1 lg:grid-cols-2 gap-6 lg:gap-10 items-center ${
                    reverse ? "lg:[&>div:first-child]:order-2" : ""
                  }`}
                >
                  <div className="rounded-xl overflow-hidden border border-sage/20 bg-sage/5 aspect-[16/10]">
                    <ScreenshotImage slug={slug} title={t.features.pages[slug].title} />
                  </div>
                  <div>
                    <h3
                      className={`text-2xl font-bold ${text.heading} mb-3`}
                    >
                      {t.features.pages[slug].title}
                    </h3>
                    <p className={`${text.muted} leading-relaxed`}>
                      {t.features.pages[slug].desc}
                    </p>
                  </div>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-6">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className={`text-4xl font-bold ${text.heading} mb-4`}>
            {t.features.ctaTitle}
          </h2>
          <p className={`${text.muted} mb-8 text-lg`}>{t.features.ctaDesc}</p>
          <div className="flex items-center justify-center gap-4">
            <Link
              to="/register"
              className="glass-btn-primary px-8 py-3 text-base"
            >
              {t.features.ctaBtn}
            </Link>
            <Link to="/" className="glass-btn-secondary px-8 py-3 text-base">
              {t.features.backToHome}
            </Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className={`border-t ${border.default} py-8 px-6`}>
        <div className="max-w-6xl mx-auto flex items-center justify-between">
          <div className={`flex items-center gap-2 ${text.muted} text-sm`}>
            <img src="/favicon.svg" alt="" className="w-5 h-5" />
            <span>Wiz Scheduler</span>
          </div>
          <div className={`text-xs ${text.muted}`}>Suggestival LLC</div>
        </div>
      </footer>
    </div>
  );
}

function ScreenshotImage({ slug, title }: { slug: Slug; title: string }) {
  return (
    <img
      src={`/screenshots/${slug}.png`}
      alt={`Screenshot of the ${title} page`}
      loading="lazy"
      className="w-full h-full object-cover object-top"
      onError={(e) => {
        const img = e.currentTarget;
        const parent = img.parentElement;
        if (!parent) return;
        img.style.display = "none";
        parent.classList.add("flex", "items-center", "justify-center");
        const placeholder = document.createElement("div");
        placeholder.className = "text-sm text-gray-500 px-4 text-center";
        placeholder.textContent = `Screenshot pending: ${title}`;
        parent.appendChild(placeholder);
      }}
    />
  );
}
