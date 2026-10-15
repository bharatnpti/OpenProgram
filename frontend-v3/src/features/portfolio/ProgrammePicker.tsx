import type { Verdict } from "../../api/schema";
import { cn } from "../../lib/utils";
import { VerdictDot } from "../hub/VerdictDot";
import { verdictWords } from "../hub/hubTabs";

/** Past this many programmes the chips become one select. */
const AS_CHIPS = 6;

export type ProgrammeChoice = { id: string; name: string; verdict: Verdict | null };

/**
 * The programme a tab shows, picked: a chip each with its date verdict's dot
 * (the worst of its projects'), worst first as given; past six programmes one
 * select instead. `every` adds "Every programme" (the empty pick) first.
 */
export function ProgrammePicker({
  programmes,
  value,
  onChange,
  every = false,
  id = "programme-pick",
}: {
  programmes: readonly ProgrammeChoice[];
  value: string;
  onChange: (id: string) => void;
  every?: boolean;
  id?: string;
}) {
  if (programmes.length < 2) return null;
  if (programmes.length > AS_CHIPS) {
    return (
      <div className="min-w-0">
        <label htmlFor={id} className="mb-1 block text-[12px] font-bold">
          Programme
        </label>
        <select
          id={id}
          value={value}
          onChange={(event) => onChange(event.target.value)}
          className="h-10 max-w-full rounded-full border border-grey-border bg-(--op-viz-surface) px-4 text-[14px] font-bold text-ink"
        >
          {every ? <option value="">Every programme</option> : null}
          {programmes.map((programme) => (
            <option key={programme.id} value={programme.id}>
              {programme.name} · {verdictWords(programme.verdict)}
            </option>
          ))}
        </select>
      </div>
    );
  }
  const chips: { id: string; name: string; verdict: Verdict | null; all?: boolean }[] = [
    ...(every ? [{ id: "", name: "Every programme", verdict: null, all: true }] : []),
    ...programmes,
  ];
  return (
    <div role="group" aria-label="Programme" className="flex min-w-0 flex-wrap gap-2">
      {chips.map((chip) => {
        const on = chip.id === value;
        return (
          <button
            key={chip.id || "every"}
            type="button"
            aria-pressed={on}
            onClick={() => onChange(chip.id)}
            className={cn(
              "inline-flex min-h-11 max-w-full items-center gap-2 rounded-full border px-3.5 text-[13px] font-bold sm:min-h-9",
              on
                ? "border-ink bg-ink text-white"
                : "border-grey-border bg-(--op-viz-surface) text-ink hover:bg-grey-fill",
            )}
          >
            {chip.all ? null : <VerdictDot verdict={chip.verdict} />}
            <span className="min-w-0 truncate">{chip.name}</span>
            {chip.all ? null : <span className="sr-only">, {verdictWords(chip.verdict)}</span>}
          </button>
        );
      })}
    </div>
  );
}
