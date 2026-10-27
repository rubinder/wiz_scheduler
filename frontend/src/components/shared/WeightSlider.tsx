interface Props {
  value: number;
  onChange: (v: number) => void;
  label: string;
  hardWarning: string;
  /** Read-only, e.g. a shared employee a regular manager cannot edit. */
  disabled?: boolean;
}

/**
 * Shared 0.0-1.0 weight control used by all three scheduling-preference
 * pages, so the "weight 1.0 is a hard filter" warning is written once. At
 * 1.0 a slot with no surviving candidate is emitted VACANT, and a manager
 * must see that consequence at the moment they choose it.
 */
export default function WeightSlider({ value, onChange, label, hardWarning, disabled }: Props) {
  return (
    <div className="flex flex-col gap-1">
      <label className="flex items-center gap-3 text-sm">
        <span className="w-24">{label}</span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.1}
          value={value}
          onChange={(e) => onChange(Number(e.target.value))}
          disabled={disabled}
          className="flex-1"
        />
        <span className="w-10 text-end tabular-nums">{value.toFixed(1)}</span>
      </label>
      {value >= 1 && (
        <p className="text-xs text-orange-700 ms-24">{hardWarning}</p>
      )}
    </div>
  );
}
