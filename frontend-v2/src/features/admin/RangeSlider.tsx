import type { ChangeEvent } from "react";

export function RangeSlider({
  label,
  min,
  max,
  step,
  value,
  valueLabel,
  onChange,
}: {
  label: string;
  min: number;
  max: number;
  step: number;
  value: number;
  valueLabel: string;
  onChange: (event: ChangeEvent<HTMLInputElement>) => void;
}) {
  return (
    <div>
      <div className="flex items-center justify-between">
        <label className="text-[13px] font-bold text-grey-secondary">{label}</label>
        <span className="text-[13px] font-bold text-ink">{valueLabel}</span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={onChange}
        className="mt-2 h-2 w-full cursor-pointer appearance-none rounded-full bg-grey-fill accent-magenta"
      />
    </div>
  );
}
