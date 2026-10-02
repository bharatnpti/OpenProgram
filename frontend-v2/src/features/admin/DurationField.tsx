import { useState } from "react";

import { TextInput } from "../../components/ui/Field";
import {
  type DurationParts,
  type DurationUnit,
  durationUnits,
  formatDuration,
  parseDuration,
  splitDuration,
} from "../../lib/duration";
import { cn } from "../../lib/utils";
import { AdminSelect } from "./AdminSelect";
import { FormField } from "./FormField";

/**
 * A wait typed as an amount and a unit, with one-click presets. It shows any
 * stored value exactly (4 h, 90 min, 61 s) instead of fitting it to a slider's
 * range, so opening a member and saving changes nothing. `onChange` gets
 * `null` while the amount can't be saved.
 */
export function DurationField({
  id,
  label,
  hint,
  value,
  presets,
  onChange,
}: {
  id: string;
  label: string;
  hint: string;
  value: number | null;
  presets: number[];
  onChange: (seconds: number | null) => void;
}) {
  const [parts, setParts] = useState<DurationParts>(() => splitDuration(value ?? 0));
  const [shown, setShown] = useState(value);
  // A different member was picked: show their stored value as it is.
  if (value !== shown) {
    setShown(value);
    if (value !== null) {
      setParts(splitDuration(value));
    }
  }
  const parsed = parseDuration(parts);

  function edit(next: DurationParts) {
    const result = parseDuration(next);
    const seconds = result.ok ? result.seconds : null;
    setParts(next);
    setShown(seconds);
    onChange(seconds);
  }

  return (
    <div>
      <FormField label={label} htmlFor={id} error={parsed.ok ? undefined : parsed.error}>
        <div className="flex gap-2">
          <TextInput
            id={id}
            type="number"
            inputMode="numeric"
            min={0}
            step={1}
            value={parts.amount}
            aria-invalid={!parsed.ok}
            onChange={(event) => edit({ ...parts, amount: event.target.value })}
            className="min-w-0 flex-1"
          />
          <AdminSelect
            aria-label={`${label} unit`}
            value={parts.unit}
            onChange={(event) => edit({ ...parts, unit: event.target.value as DurationUnit })}
            className="w-36"
          >
            {durationUnits.map((option) => (
              <option key={option.unit} value={option.unit}>
                {option.label}
              </option>
            ))}
          </AdminSelect>
        </div>
      </FormField>
      <p className="mt-1.5 text-[12px] text-grey-secondary">{hint}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        {presets.map((seconds) => (
          <button
            key={seconds}
            type="button"
            aria-pressed={value === seconds}
            onClick={() => edit(splitDuration(seconds))}
            className={cn(
              "flex h-8 items-center rounded-full border px-3 text-[12px] font-bold",
              value === seconds
                ? "border-magenta bg-magenta text-white"
                : "border-grey-border text-grey-secondary hover:bg-grey-fill",
            )}
          >
            {formatDuration(seconds)}
          </button>
        ))}
      </div>
    </div>
  );
}
