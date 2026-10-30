import { usePrograms } from "../../app/directory";
import { usePortfolioIndex } from "../hub/useHub";
import type { ProgrammeChoice } from "./ProgrammePicker";
import { worstVerdict } from "./programmeWords";

const WEIGHT: Record<string, number> = {
  off_track: 5,
  no_date: 4,
  at_risk: 3,
  not_enough_data: 2,
  on_track: 1,
  done: 0,
};

/**
 * Every programme, each with its date verdict (the worst of its projects', from
 * the hub's bulk read of kept rows), worst first, then by name: the programme
 * picker's chips. A programme the bulk read does not count has no verdict yet.
 */
export function useProgrammes(): { programmes: ProgrammeChoice[]; isLoading: boolean } {
  const programs = usePrograms();
  const index = usePortfolioIndex();
  const counts = new Map(
    (index.data?.programs ?? []).map((program) => [program.id, program.counts]),
  );
  const programmes = (programs.data ?? [])
    .map((program) => ({
      id: program.id,
      name: program.name,
      verdict: worstVerdict(counts.get(program.id)),
    }))
    .sort(
      (a, b) =>
        (b.verdict ? WEIGHT[b.verdict] : -1) - (a.verdict ? WEIGHT[a.verdict] : -1) ||
        a.name.localeCompare(b.name),
    );
  return { programmes, isLoading: programs.isLoading };
}
