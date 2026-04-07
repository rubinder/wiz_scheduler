import { Link } from "react-router-dom";
import { useLanguage } from "../i18n/LanguageContext";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import LanguageSelector from "../components/shared/LanguageSelector";

export default function Landing() {
  const { t } = useLanguage();
  useDocumentTitle("AI-Powered Employee Scheduling");

  const features = [
    { title: t.landing.featAITitle, desc: t.landing.featAIDesc },
    { title: t.landing.featStrategiesTitle, desc: t.landing.featStrategiesDesc },
    { title: t.landing.featMultiLocTitle, desc: t.landing.featMultiLocDesc },
    { title: t.landing.featSelfServiceTitle, desc: t.landing.featSelfServiceDesc },
    { title: t.landing.feat7shiftsTitle, desc: t.landing.feat7shiftsDesc },
    { title: t.landing.featGDPRTitle, desc: t.landing.featGDPRDesc },
    { title: t.landing.featAffinitiesTitle, desc: t.landing.featAffinitiesDesc },
    { title: t.landing.featRoleEquivTitle, desc: t.landing.featRoleEquivDesc },
    { title: t.landing.featLangsTitle, desc: t.landing.featLangsDesc },
  ];

  const strategies = [
    {
      name: t.landing.stratRotation,
      tag: t.landing.stratRotationTag,
      badge: "bg-emerald-500/15 text-emerald-300 border border-emerald-400/20",
      desc: t.landing.stratRotationDesc,
    },
    {
      name: t.landing.stratRotationHistory,
      tag: t.landing.stratRotationHistoryTag,
      badge: "bg-emerald-500/15 text-emerald-300 border border-emerald-400/20",
      desc: t.landing.stratRotationHistoryDesc,
    },
    {
      name: t.landing.stratMaxHours,
      tag: t.landing.stratMaxHoursTag,
      badge: "bg-emerald-500/15 text-emerald-300 border border-emerald-400/20",
      desc: t.landing.stratMaxHoursDesc,
    },
    {
      name: t.landing.stratAI,
      tag: t.landing.stratAITag,
      badge: "bg-purple-500/15 text-purple-300 border border-purple-400/20",
      desc: t.landing.stratAIDesc,
    },
  ];

  const compliance = [
    { title: t.landing.compDataExportTitle, desc: t.landing.compDataExportDesc },
    { title: t.landing.compErasureTitle, desc: t.landing.compErasureDesc },
    { title: t.landing.compConsentTitle, desc: t.landing.compConsentDesc },
    { title: t.landing.compNoNamesTitle, desc: t.landing.compNoNamesDesc },
  ];

  const jsonLd = {
    "@context": "https://schema.org",
    "@type": "SoftwareApplication",
    name: "Wiz Scheduler",
    applicationCategory: "BusinessApplication",
    operatingSystem: "Web",
    description:
      "AI-powered employee scheduling that automatically respects availability, roles, skill levels, and team preferences.",
    offers: {
      "@type": "Offer",
      price: "0",
      priceCurrency: "USD",
    },
  };

  return (
    <div className="min-h-screen">
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      {/* Nav */}
      <nav className="fixed top-0 inset-x-0 z-50 bg-white/[0.03] backdrop-blur-2xl border-b border-white/[0.06]">
        <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <img src="/favicon.svg" alt="" className="w-8 h-8" />
            <span className="text-xl font-bold text-white tracking-wide">Wiz Scheduler</span>
          </div>
          <div className="flex items-center gap-4">
            <LanguageSelector />
            <Link to="/login" className="text-sm text-gray-300 hover:text-white transition-colors">
              {t.login.signIn}
            </Link>
            <Link
              to="/register"
              className="glass-btn-primary text-sm"
            >
              {t.register.registerBtn}
            </Link>
          </div>
        </div>
      </nav>

      {/* Hero */}
      <section className="pt-32 pb-20 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-purple-500/10 border border-purple-400/20 text-purple-300 text-sm mb-8">
            {t.landing.badge}
          </div>
          <h1 className="text-5xl sm:text-6xl font-extrabold text-white leading-tight mb-6">
            {t.landing.heroTitle}{" "}
            <span className="bg-gradient-to-r from-purple-400 to-cyan-400 bg-clip-text text-transparent">
              {t.landing.heroTitleAccent}
            </span>
          </h1>
          <p className="text-xl text-gray-400 max-w-2xl mx-auto mb-10 leading-relaxed">
            {t.landing.heroDesc}
          </p>
          <div className="flex items-center justify-center gap-4">
            <Link to="/register" className="glass-btn-primary px-8 py-3 text-base">
              {t.landing.getStarted}
            </Link>
            <a href="#pricing" className="glass-btn-secondary px-8 py-3 text-base">
              {t.landing.viewPricing}
            </a>
            <a href="#demo" className="glass-btn-secondary px-8 py-3 text-base">
              {t.landing.viewDemo}
            </a>
          </div>
        </div>
      </section>

      {/* Features */}
      <section className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-white text-center mb-4">{t.landing.featuresTitle}</h2>
          <p className="text-gray-400 text-center mb-16 max-w-2xl mx-auto">
            {t.landing.featuresDesc}
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {features.map((f) => (
              <div key={f.title} className="glass-card p-6 hover:bg-white/[0.1] transition-all">
                <h3 className="text-lg font-semibold text-white mb-2">{f.title}</h3>
                <p className="text-sm text-gray-400 leading-relaxed">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Demo Video */}
      <section id="demo" className="py-20 px-6">
        <div className="max-w-4xl mx-auto">
          <h2 className="text-3xl font-bold text-white text-center mb-4">{t.landing.demoTitle}</h2>
          <p className="text-gray-400 text-center mb-12 max-w-2xl mx-auto">
            {t.landing.demoDesc}
          </p>
          <div className="glass-card p-2 rounded-2xl overflow-hidden">
            <div className="relative w-full" style={{ paddingBottom: "56.25%" }}>
              <video
                className="absolute inset-0 w-full h-full rounded-xl"
                controls
                preload="metadata"
                poster="/demo-poster.jpg"
              >
                <source src="/demo.mp4" type="video/mp4" />
                {t.landing.demoFallback}
              </video>
            </div>
          </div>
        </div>
      </section>

      {/* Strategies */}
      <section className="py-20 px-6">
        <div className="max-w-6xl mx-auto">
          <h2 className="text-3xl font-bold text-white text-center mb-4">{t.landing.strategiesTitle}</h2>
          <p className="text-gray-400 text-center mb-16 max-w-2xl mx-auto">
            {t.landing.strategiesDesc}
          </p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {strategies.map((s) => (
              <div key={s.name} className="glass-card p-6">
                <div className="flex items-center gap-3 mb-3">
                  <span className={`inline-block px-3 py-1 rounded-full text-xs font-semibold ${s.badge}`}>
                    {s.tag}
                  </span>
                  <h3 className="text-lg font-semibold text-white">{s.name}</h3>
                </div>
                <p className="text-sm text-gray-400 leading-relaxed">{s.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* Pricing */}
      <section id="pricing" className="py-20 px-6">
        <div className="max-w-5xl mx-auto">
          <h2 className="text-3xl font-bold text-white text-center mb-4">{t.landing.pricingTitle}</h2>
          <p className="text-gray-400 text-center mb-16 max-w-2xl mx-auto">
            {t.landing.pricingDesc}
          </p>

          {/* Base plan */}
          <div className="glass-card p-8 mb-10 text-center">
            <div className="inline-flex items-center gap-2 px-4 py-1.5 rounded-full bg-purple-500/10 border border-purple-400/20 text-purple-300 text-sm mb-4">
              {t.landing.allInOnePlan}
            </div>
            <div className="text-5xl font-extrabold text-white mb-2">$15<span className="text-xl font-normal text-gray-400"> {t.landing.pricePerMonth}</span></div>
            <p className="text-gray-400 mb-8">{t.landing.basePlanDesc}</p>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-6 max-w-4xl mx-auto">
              <div>
                <div className="text-2xl font-bold text-white">$2.00</div>
                <div className="text-sm text-gray-400 mt-1">{t.landing.aiCredits}</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-white">50</div>
                <div className="text-sm text-gray-400 mt-1">{t.landing.compSchedules}</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-white">0.5 GB</div>
                <div className="text-sm text-gray-400 mt-1">{t.landing.storageIncluded}</div>
              </div>
              <div>
                <div className="text-2xl font-bold text-white">33K</div>
                <div className="text-sm text-gray-400 mt-1">{t.landing.employeesIncluded}</div>
              </div>
            </div>
            <p className="text-gray-500 text-sm mt-6">
              {t.landing.normalStrategiesNote}
            </p>
          </div>

          {/* Overage pricing */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
            <div className="glass-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-9 h-9 rounded-lg bg-purple-500/15 border border-purple-400/20 flex items-center justify-center text-purple-300 text-sm font-bold">
                  AI
                </div>
                <h3 className="text-base font-semibold text-white">{t.landing.aiScheduling}</h3>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between text-gray-300">
                  <span>{t.landing.included}</span>
                  <span className="text-white font-medium">$2.00 / mo</span>
                </div>
                <div className="border-t border-white/[0.06] pt-2 flex justify-between text-gray-300">
                  <span>{t.landing.overage}</span>
                  <span className="text-white font-medium">Cost x 130%</span>
                </div>
                <p className="text-xs text-gray-500 pt-1">
                  {t.landing.aiOverageNote}
                </p>
              </div>
            </div>

            <div className="glass-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-9 h-9 rounded-lg bg-amber-500/15 border border-amber-400/20 flex items-center justify-center text-amber-300 text-sm font-bold">
                  GEN
                </div>
                <h3 className="text-base font-semibold text-white">{t.landing.schedules}</h3>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between text-gray-300">
                  <span>{t.landing.included}</span>
                  <span className="text-white font-medium">50 / month</span>
                </div>
                <div className="border-t border-white/[0.06] pt-2 flex justify-between text-gray-300">
                  <span>{t.landing.overage}</span>
                  <span className="text-white font-medium">$0.10 / 50</span>
                </div>
                <p className="text-xs text-gray-500 pt-1">
                  {t.landing.schedulesOverageNote}
                </p>
              </div>
            </div>

            <div className="glass-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-9 h-9 rounded-lg bg-cyan-500/15 border border-cyan-400/20 flex items-center justify-center text-cyan-300 text-sm font-bold">
                  DB
                </div>
                <h3 className="text-base font-semibold text-white">{t.landing.storage}</h3>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between text-gray-300">
                  <span>{t.landing.included}</span>
                  <span className="text-white font-medium">0.5 GB</span>
                </div>
                <div className="border-t border-white/[0.06] pt-2 flex justify-between text-gray-300">
                  <span>{t.landing.overage}</span>
                  <span className="text-white font-medium">$0.50 / GB</span>
                </div>
                <p className="text-xs text-gray-500 pt-1">
                  {t.landing.storageOverageNote}
                </p>
              </div>
            </div>

            <div className="glass-card p-5">
              <div className="flex items-center gap-2 mb-3">
                <div className="w-9 h-9 rounded-lg bg-emerald-500/15 border border-emerald-400/20 flex items-center justify-center text-emerald-300 text-sm font-bold">
                  #
                </div>
                <h3 className="text-base font-semibold text-white">{t.landing.employees}</h3>
              </div>
              <div className="space-y-2 text-sm">
                <div className="flex justify-between text-gray-300">
                  <span>{t.landing.included}</span>
                  <span className="text-white font-medium">33,000</span>
                </div>
                <div className="border-t border-white/[0.06] pt-2 flex justify-between text-gray-300">
                  <span>{t.landing.overage}</span>
                  <span className="text-white font-medium">$0.20 / 33K</span>
                </div>
                <p className="text-xs text-gray-500 pt-1">
                  {t.landing.employeesOverageNote}
                </p>
              </div>
            </div>
          </div>

          {/* Example */}
          <div className="glass-card p-6 mt-10">
            <h3 className="text-lg font-semibold text-white mb-4">{t.landing.exampleTitle}</h3>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left text-gray-400 border-b border-white/[0.06]">
                    <th className="pb-3 font-medium">{t.landing.exampleItem}</th>
                    <th className="pb-3 font-medium">{t.landing.exampleUsage}</th>
                    <th className="pb-3 font-medium text-right">{t.landing.exampleCost}</th>
                  </tr>
                </thead>
                <tbody className="text-gray-300">
                  <tr className="border-b border-white/[0.04]">
                    <td className="py-3">{t.landing.exampleBase}</td>
                    <td></td>
                    <td className="text-right text-white font-medium">$15.00</td>
                  </tr>
                  <tr className="border-b border-white/[0.04]">
                    <td className="py-3">{t.landing.exampleSchedules}</td>
                    <td>{t.landing.exampleSchedulesUsage}</td>
                    <td className="text-right text-emerald-400 font-medium">{t.landing.exampleSchedulesCost}</td>
                  </tr>
                  <tr className="border-b border-white/[0.04]">
                    <td className="py-3">{t.landing.exampleAI}</td>
                    <td>{t.landing.exampleAIUsage}</td>
                    <td className="text-right text-emerald-400 font-medium">{t.landing.exampleAICost}</td>
                  </tr>
                  <tr className="border-b border-white/[0.04]">
                    <td className="py-3">{t.landing.exampleStorage}</td>
                    <td>{t.landing.exampleStorageUsage}</td>
                    <td className="text-right text-emerald-400 font-medium">{t.landing.exampleStorageCost}</td>
                  </tr>
                  <tr className="border-b border-white/[0.04]">
                    <td className="py-3">{t.landing.exampleEmployees}</td>
                    <td>{t.landing.exampleEmployeesUsage}</td>
                    <td className="text-right text-emerald-400 font-medium">{t.landing.exampleEmployeesCost}</td>
                  </tr>
                  <tr>
                    <td className="py-3 font-semibold text-white">{t.landing.exampleTotal}</td>
                    <td></td>
                    <td className="text-right text-white font-bold text-lg">$15.00</td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </div>
      </section>

      {/* GDPR */}
      <section className="py-20 px-6">
        <div className="max-w-4xl mx-auto text-center">
          <h2 className="text-3xl font-bold text-white mb-4">{t.landing.complianceTitle}</h2>
          <p className="text-gray-400 mb-10 max-w-2xl mx-auto">
            {t.landing.complianceDesc}
          </p>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            {compliance.map((c) => (
              <div key={c.title} className="glass-card p-5 text-center">
                <h4 className="text-sm font-semibold text-white mb-1">{c.title}</h4>
                <p className="text-xs text-gray-500">{c.desc}</p>
              </div>
            ))}
          </div>
          <div className="mt-8 flex items-center justify-center gap-4 text-sm">
            <Link to="/privacy-policy" className="text-purple-400 hover:text-purple-300">{t.gdpr.privacyPolicy}</Link>
            <span className="text-gray-600">|</span>
            <Link to="/terms" className="text-purple-400 hover:text-purple-300">{t.gdpr.termsOfService}</Link>
            <span className="text-gray-600">|</span>
            <Link to="/dpa" className="text-purple-400 hover:text-purple-300">{t.gdpr.dpa}</Link>
          </div>
        </div>
      </section>

      {/* CTA */}
      <section className="py-20 px-6">
        <div className="max-w-3xl mx-auto text-center">
          <h2 className="text-4xl font-bold text-white mb-4">{t.landing.ctaTitle}</h2>
          <p className="text-gray-400 mb-8 text-lg">
            {t.landing.ctaDesc}
          </p>
          <div className="flex items-center justify-center gap-4">
            <Link to="/register" className="glass-btn-primary px-8 py-3 text-base">
              {t.landing.ctaBtn}
            </Link>
            <Link to="/login" className="glass-btn-secondary px-8 py-3 text-base">
              {t.login.signIn}
            </Link>
          </div>
        </div>
      </section>

      {/* Footer */}
      <footer className="border-t border-white/[0.06] py-8 px-6">
        <div className="max-w-6xl mx-auto flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="flex items-center gap-2 text-gray-500 text-sm">
            <img src="/favicon.svg" alt="" className="w-5 h-5" />
            <span>Wiz Scheduler</span>
          </div>
          <div className="flex items-center gap-4 text-xs text-gray-500">
            <Link to="/privacy-policy" className="hover:text-gray-300">{t.gdpr.privacyPolicy}</Link>
            <Link to="/terms" className="hover:text-gray-300">{t.gdpr.termsOfService}</Link>
            <Link to="/dpa" className="hover:text-gray-300">{t.gdpr.dpa}</Link>
          </div>
        </div>
      </footer>
    </div>
  );
}

