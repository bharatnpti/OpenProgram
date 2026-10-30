import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { PersonaTreeNodeDto, Rag } from "../../api/schema";
import { useRole } from "../../app/role";
import { useDayWords } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { RagDot } from "../../components/ui/Bits";
import { Fold } from "../../components/viz/Fold";
import { formatTime } from "../../lib/format";
import { cn } from "../../lib/utils";
import { checkinsLine } from "../../lib/words";
import { deviceTimezone } from "../../lib/zones";
import { FactorsPanel } from "../delivery/NodeBits";
import { setBy } from "../delivery/factors";
import { programCell, programmeVerdict } from "./heat";
import { useHeatmap } from "./useHeatmap";
import { rollUpChips } from "./programmeWords";
import { VizPanel } from "./VizPanel";

const HERO: Record<Rag, string> = {
  red: "bg-rag-red-bg text-rag-red",
  amber: "bg-rag-amber-bg text-rag-amber-deep",
  green: "bg-rag-green-bg text-rag-green",
  unknown: "bg-grey-fill text-grey-body",
};

/**
 * A programme's health today, at the top of All projects › Early warnings, for
 * the readers of programmes (manager, executive, admin): its headline and what
 * follows it, the day's check-ins, the one reason that sets its colour, what
 * rolls up into it, and every reason, folded. Today's health, not the date. An
 * executive reads it as counts: the server names nobody, and the reasons'
 * people are not listed.
 */
export function ProgrammeHealth({ programId, name }: { programId: string; name: string }) {
  const { access } = useRole();
  const canReadPortfolio = access.hub.portfolioHealth;
  const names = !access.hub.countsOnly;
  const day = useDayWords();
  const attention = useQuery({
    queryKey: ["portfolio", "attention", programId],
    queryFn: () =>
      apiClient.portfolioAttention(undefined, programId, deviceTimezone() ?? undefined),
    enabled: canReadPortfolio && programId !== "",
  });
  // Its own cell can be worse than its teams': it then leads the headline.
  const heatmap = useHeatmap(programId, canReadPortfolio);
  const tree = useQuery({
    queryKey: ["program", programId, "tree"],
    queryFn: () => apiClient.personaProgramTree(programId),
    enabled: canReadPortfolio && programId !== "",
  });

  const a = attention.data;
  const verdict = a ? programmeVerdict(a, programCell(heatmap.data?.cells, programId)) : null;
  const nodes = tree.data?.nodes ?? [];
  const root: PersonaTreeNodeDto | undefined =
    nodes.find((node) => node.id === programId) ?? nodes.find((node) => node.kind === "program");
  const sets = root ? setBy(root.rag, root.factors) : null;
  const sourceNames = Object.fromEntries(nodes.map((node) => [node.id, node.name]));
  const chips = rollUpChips(nodes, root?.people ?? null);

  return (
    <VizPanel title={`${name}: today's health`} titleId="programme-health-h">
      <PanelState
        // The heat map too: its programme cell can turn the headline, and an amber one
        // drawn first would flip to red a moment later.
        isLoading={attention.isLoading || heatmap.isLoading}
        error={attention.error}
        onRetry={() => void attention.refetch()}
      >
        {a && verdict ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <div className={cn("rounded-2xl p-5 max-sm:p-4", HERO[verdict.rag])}>
              <p className="flex items-center gap-3 text-[20px] font-extrabold text-balance max-sm:text-[17px]">
                <RagDot rag={verdict.rag} className="h-3 w-3 flex-none" />
                {verdict.headline}
              </p>
              {verdict.detail ? (
                <p className="mt-2 text-[14px] font-medium">{verdict.detail}</p>
              ) : null}
              {a.checkins ? (
                <p className="mt-3 text-[13px] opacity-80">
                  {checkinsLine(
                    a.checkins,
                    a.checkins.first_asked_at ? formatTime(a.checkins.first_asked_at) : null,
                    day,
                  )}
                </p>
              ) : null}
            </div>
            <PanelState isLoading={tree.isLoading} error={tree.error}>
              {root ? (
                <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
                  {sets ? (
                    <p className="text-[14px] text-grey-body">
                      <span className="font-bold text-ink">Set by:</span> {sets.text}
                    </p>
                  ) : null}
                  {chips.length > 0 ? (
                    <ul
                      className="flex flex-wrap gap-2 text-[13px] text-grey-body"
                      aria-label="What rolls up into it"
                    >
                      {chips.map((chip) => (
                        <li key={chip} className="rounded-full bg-grey-fill px-3 py-1 font-bold">
                          {chip}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {/* An executive reads the colour, never the people behind its reasons. */}
                  {names && root.factors.length > 1 ? (
                    <Fold summary="Every reason behind today's health">
                      <FactorsPanel factors={root.factors} names={sourceNames} always />
                    </Fold>
                  ) : null}
                </div>
              ) : null}
            </PanelState>
          </div>
        ) : null}
      </PanelState>
    </VizPanel>
  );
}
