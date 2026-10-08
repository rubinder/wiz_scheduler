import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { MarketingShell } from "../components/marketing/MarketingNav";
import { marketing as m } from "../theme";

export default function About() {
  useDocumentTitle("About Us");

  return (
    <MarketingShell>
      <main className="max-w-[92rem] mx-auto px-6">
        {/* Hero */}
        <section className="pt-12 lg:pt-20 pb-16">
          <div className="max-w-[62ch] text-start">
            <h1
              className={`${m.text.display} font-display text-5xl lg:text-6xl font-semibold leading-[0.95] mb-6`}
            >
              About Wiz Scheduler
            </h1>
            <p className={`${m.text.muted} text-lg leading-relaxed max-w-[62ch]`}>
              We're building the AI-powered scheduling tool that restaurants and retail teams actually want to use.
            </p>
          </div>
        </section>

        {/* Mission */}
        <section className="max-w-3xl mb-16 pb-16 border-b border-gray-200">
          <h2 className={`${m.text.display} text-3xl font-semibold mb-4`}>Our Mission</h2>
          <p className={`${m.text.muted} text-base leading-relaxed mb-4`}>
            Scheduling shouldn't be painful. Managers spend hours wrestling with spreadsheets, trying to balance team
            preferences, compliance rules, and business needs. Employees never know their schedule until the last minute.
          </p>
          <p className={`${m.text.muted} text-base leading-relaxed`}>
            We're changing that. Wiz Scheduler uses AI to generate fair, optimized schedules in seconds. It respects
            availability, skill levels, team affinities, and local labor laws—so managers can focus on running great
            teams, not managing schedules.
          </p>
        </section>

        {/* Team */}
        <section className="max-w-3xl mb-16 pb-16 border-b border-gray-200">
          <h2 className={`${m.text.display} text-3xl font-semibold mb-6`}>Our Team</h2>
          <p className={`${m.text.muted} text-base leading-relaxed mb-6`}>
            Wiz Scheduler is built by Suggestival LLC, a small team obsessed with solving real problems for restaurant
            and retail operators.
          </p>
          <p className={`${m.text.muted} text-base leading-relaxed mb-6`}>
            We've worked in hospitality. We know how chaos the scheduling can be. We built this tool for people like us.
          </p>
        </section>

        {/* Values */}
        <section className="max-w-3xl">
          <h2 className={`${m.text.display} text-3xl font-semibold mb-6`}>Our Values</h2>
          <div className="space-y-6">
            <div>
              <h3 className="font-semibold text-lg mb-2">Bias-free scheduling</h3>
              <p className={`${m.text.muted} text-base`}>
                AI should remove bias, not amplify it. We don't use names in scheduling decisions. Ever.
              </p>
            </div>
            <div>
              <h3 className="font-semibold text-lg mb-2">Privacy first</h3>
              <p className={`${m.text.muted} text-base`}>
                Your team's data stays yours. We don't train models on your data. No names sent to AI. Full transparency.
              </p>
            </div>
            <div>
              <h3 className="font-semibold text-lg mb-2">Compliance, not compromise</h3>
              <p className={`${m.text.muted} text-base`}>
                Fair Workweek, wage-and-hour laws, seniority rules. Real compliance, not checkboxes.
              </p>
            </div>
            <div>
              <h3 className="font-semibold text-lg mb-2">Affordable for small teams</h3>
              <p className={`${m.text.muted} text-base`}>
                Scheduling software shouldn't cost more than your POS system. Free tier for single-location shops. $18/mo
                per location for growth.
              </p>
            </div>
          </div>
        </section>
      </main>
    </MarketingShell>
  );
}
