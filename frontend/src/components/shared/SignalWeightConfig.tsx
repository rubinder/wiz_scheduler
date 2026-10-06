import WeightSlider from "./WeightSlider";
import type { SignalConfig } from "../../types";

interface Props {
  config: SignalConfig | null;
  onChange: (config: SignalConfig) => void;
  readOnly?: boolean;
}

/**
 * Signal weight configuration display with sliders for tunable signals.
 * Allows managers to set default weights at company or location level.
 *
 * Signals:
 * - Seniority: Favor senior employees (hire_date or manual rank)
 * - Pay: Favor lower-wage employees (cost optimization)
 * - Overtime: Minimize overtime hours
 *
 * Note: Affinity (team preferences) is always applied and not tunable,
 * similar to other hard preferences (day preferences, hour ranges, etc).
 */
export default function SignalWeightConfig({ config, onChange, readOnly = false }: Props) {
  const weights = config || {
    seniority_weight: 0.0,
    pay_weight: 0.0,
    overtime_weight: 0.0,
  };

  const handleChange = (key: keyof SignalConfig, value: number) => {
    if (readOnly) return;
    onChange({
      ...weights,
      [key]: value,
    });
  };

  return (
    <div className="flex flex-col gap-4 p-4 border border-gray-200 rounded-lg bg-gray-50">
      <div className="flex flex-col gap-1">
        <h3 className="text-sm font-semibold text-gray-900">Signal Weight Defaults</h3>
        <p className="text-xs text-gray-600">
          Configure how scheduling decisions weight different factors. Higher = more important.
        </p>
      </div>

      <div className="space-y-3">
        <WeightSlider
          label="Seniority"
          value={weights.seniority_weight}
          onChange={(v) => handleChange("seniority_weight", v)}
          hardWarning="At 1.0, only the most senior employee will be scheduled for each shift."
        />

        <WeightSlider
          label="Pay Rate"
          value={weights.pay_weight}
          onChange={(v) => handleChange("pay_weight", v)}
          hardWarning="At 1.0, only the lowest-wage employees will be scheduled for each shift."
        />

        <WeightSlider
          label="Overtime"
          value={weights.overtime_weight}
          onChange={(v) => handleChange("overtime_weight", v)}
          hardWarning="At 1.0, no employee over the overtime threshold will be scheduled."
        />
      </div>

      <div className="text-xs text-gray-500 bg-white p-2 rounded border border-gray-200">
        💡 Tip: These are default weights that apply to all schedules generated at this level,
        unless overridden during schedule generation.
      </div>
    </div>
  );
}
