import { Link } from "react-router-dom";
import { useDocumentTitle } from "../hooks/useDocumentTitle";
import { MarketingShell } from "../components/marketing/MarketingNav";
import { marketing as m } from "../theme";

export default function Pricing() {
  useDocumentTitle("Pricing");

  const plans = [
    {
      name: "Free",
      price: "$0",
      description: "Perfect for getting started",
      features: [
        "Up to 2 locations",
        "Up to 25 employees",
        "1 schedule generation per location per month",
        "2 attempts per location (retry if needed)",
        "Local scheduling (no AI)",
      ],
      cta: "Start Free",
      highlighted: false,
    },
    {
      name: "Pro",
      price: "$18",
      period: "/month per location",
      description: "For growing teams",
      features: [
        "Unlimited locations",
        "Unlimited employees",
        "Unlimited schedule generations",
        "All strategies (AI, Rotation, etc)",
        "Team preferences & affinities",
        "Export to 7shifts, Deputy",
        "Priority support",
      ],
      cta: "Start Free Trial",
      highlighted: true,
    },
  ];

  return (
    <MarketingShell>
      <main className="max-w-[92rem] mx-auto px-6">
        {/* Hero */}
        <section className="pt-12 lg:pt-20 pb-16">
          <div className="max-w-[62ch] text-start">
            <h1
              className={`${m.text.display} font-display text-5xl lg:text-6xl font-semibold leading-[0.95] mb-6`}
            >
              Simple, Transparent Pricing
            </h1>
            <p className={`${m.text.muted} text-lg leading-relaxed max-w-[62ch]`}>
              No hidden fees. Start free, upgrade when you're ready. Pay only for what you use.
            </p>
          </div>
        </section>

        {/* Pricing Cards */}
        <section className="pb-20">
          <div className="grid md:grid-cols-2 gap-8 max-w-5xl">
            {plans.map((plan) => (
              <div
                key={plan.name}
                className={`rounded-lg border p-8 ${
                  plan.highlighted
                    ? `border-blue-500 bg-blue-50/30 shadow-lg`
                    : `border-gray-200 bg-white`
                }`}
              >
                <div className="mb-6">
                  <h3 className={`${m.text.display} text-2xl font-semibold mb-2`}>
                    {plan.name}
                  </h3>
                  <div className="flex items-baseline gap-1 mb-2">
                    <span className="text-4xl font-bold">{plan.price}</span>
                    {plan.period && (
                      <span className={`${m.text.muted} text-sm`}>{plan.period}</span>
                    )}
                  </div>
                  <p className={`${m.text.muted} text-sm`}>{plan.description}</p>
                </div>

                <Link
                  to="/register"
                  className={`w-full inline-block text-center py-2 px-4 rounded font-medium mb-8 transition ${
                    plan.highlighted
                      ? "bg-blue-600 text-white hover:bg-blue-700"
                      : "bg-gray-100 text-gray-900 hover:bg-gray-200"
                  }`}
                >
                  {plan.cta}
                </Link>

                <ul className="space-y-3">
                  {plan.features.map((feature, idx) => (
                    <li key={idx} className="flex gap-3 items-start">
                      <span className="text-green-600 font-bold flex-shrink-0">✓</span>
                      <span className={`${m.text.body} text-sm`}>{feature}</span>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        </section>

        {/* FAQ */}
        <section className="max-w-3xl pb-20">
          <h2 className={`${m.text.display} text-3xl font-semibold mb-8`}>FAQ</h2>
          <div className="space-y-6">
            <div>
              <h3 className="font-semibold mb-2">Can I cancel anytime?</h3>
              <p className={`${m.text.muted} text-sm`}>
                Yes. Upgrade or downgrade your plan at any time. No long-term contracts.
              </p>
            </div>
            <div>
              <h3 className="font-semibold mb-2">How does billing work?</h3>
              <p className={`${m.text.muted} text-sm`}>
                Pro plan is billed monthly per location. For example, 3 locations = $54/month.
              </p>
            </div>
            <div>
              <h3 className="font-semibold mb-2">What's included in the free plan?</h3>
              <p className={`${m.text.muted} text-sm`}>
                The free plan includes 1 location, up to 5 employees, and 5 schedule
                generations per month. Perfect for trying out the scheduler.
              </p>
            </div>
            <div>
              <h3 className="font-semibold mb-2">Is there a setup fee?</h3>
              <p className={`${m.text.muted} text-sm`}>
                No setup fees. Start generating schedules immediately after signing up.
              </p>
            </div>
          </div>
        </section>
      </main>
    </MarketingShell>
  );
}
