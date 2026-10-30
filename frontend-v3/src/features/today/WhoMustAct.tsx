import { useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { ProjectAsksResponse } from "../../api/schema";
import { projectPath } from "../../app/links";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { readState } from "../../lib/readState";
import { raisedStillOpen, waitingStillOpen } from "../coordination/raised";
import { VizCard } from "../overall/viz";
import { WHO_MUST_ACT_SHOWN, askAge, askWho, askWhy, mergeAsks } from "./todayWords";

const asksOf = (results: { data?: ProjectAsksResponse }[]) => results.map((r) => r.data);

/**
 * Who must act: the day report's asks for the projects the person looks after,
 * one flat table by the item (what, who, how long, why it matters), the level
 * it reached first, then how long it waited. Never grouped, totalled or ranked
 * by person; an executive reads the owning team instead of a name, as the
 * server sends it. `teams` narrows a scrum master's to the pods they run.
 */
export function WhoMustAct({
  projectIds,
  teams,
  empty = "Nobody needs to act on anything today.",
}: {
  projectIds: string[];
  teams?: ReadonlySet<string>;
  empty?: string;
}) {
  const [all, setAll] = useState(false);
  const reads = useQueries({
    queries: projectIds.map((projectId) => ({
      queryKey: ["project", projectId, "asks"],
      queryFn: () => apiClient.projectAsks(projectId),
    })),
  });
  // The same reads as Your asks below (one fetch each): the requests it lists.
  const waiting = useQuery({
    queryKey: ["requests", "mine", "waiting"],
    queryFn: () => apiClient.myCrossPersonRequests("waiting"),
  });
  const raised = useQuery({
    queryKey: ["requests", "mine", "raised"],
    queryFn: () => apiClient.myCrossPersonRequests("raised"),
  });
  const yours = new Set([
    ...(waiting.data?.requests ?? []).filter((r) => waitingStillOpen(r.status)).map((r) => r.id),
    ...(raised.data?.requests ?? []).filter((r) => raisedStillOpen(r.status)).map((r) => r.id),
  ]);
  const lists = asksOf(reads);
  const asks = mergeAsks(
    lists.map((list) => list?.asks ?? []),
    teams,
    yours,
  );
  const shown = all ? asks : asks.slice(0, WHO_MUST_ACT_SHOWN);
  const single = projectIds.length === 1 ? lists[0] : undefined;
  const state =
    projectIds.length === 0
      ? { isLoading: false, error: null }
      : readState(reads[0], ...reads.slice(1));

  return (
    <VizCard aria-label="Who must act" className="gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[17px] font-extrabold">Who must act</h2>
        {single ? (
          <Link to={projectPath(single.project_id)} className="text-[12px] font-bold">
            {single.project_name}
          </Link>
        ) : null}
      </div>
      <PanelState
        {...state}
        onRetry={() => reads.forEach((read) => void read.refetch())}
        isEmpty={asks.length === 0}
        emptyText={empty}
      >
        <table className="w-full table-fixed border-collapse text-left text-[13.5px] max-md:block">
          <thead className="max-md:hidden">
            <tr className="text-[12px] uppercase tracking-wider text-grey-secondary">
              <th scope="col" className="w-[40%] pb-2 font-bold">
                What
              </th>
              <th scope="col" className="w-[18%] pb-2 font-bold">
                Who
              </th>
              <th scope="col" className="w-[10%] pb-2 font-bold">
                Age
              </th>
              <th scope="col" className="pb-2 font-bold">
                Why it matters
              </th>
            </tr>
          </thead>
          <tbody className="max-md:grid max-md:gap-3">
            {shown.map((ask, index) => (
              <tr
                key={`${ask.issue_key ?? ""}-${ask.what}-${index}`}
                className="border-t border-grey-border align-top max-md:grid max-md:gap-0.5 max-md:pt-3"
              >
                <td className="py-2 pr-3 font-bold text-ink max-md:py-0">{ask.what}</td>
                <td className="py-2 pr-3 text-grey-body max-md:py-0">
                  <span className="md:hidden text-grey-secondary">Who: </span>
                  {askWho(ask)}
                </td>
                <td className="py-2 pr-3 whitespace-nowrap text-grey-body max-md:py-0">
                  <span className="md:hidden text-grey-secondary">Waiting: </span>
                  {askAge(ask.waited_days)}
                </td>
                <td className="py-2 text-grey-secondary max-md:py-0">{askWhy(ask)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {asks.length > WHO_MUST_ACT_SHOWN ? (
          <div className="mt-2">
            <Pill size="sm" variant="ghost" onClick={() => setAll((open) => !open)}>
              {all ? "Show fewer" : `Show all ${asks.length}`}
            </Pill>
          </div>
        ) : null}
      </PanelState>
    </VizCard>
  );
}
