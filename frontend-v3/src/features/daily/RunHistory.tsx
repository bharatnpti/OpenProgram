import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { PanelState } from "../../components/PanelState";
import { Fold } from "../../components/viz/Fold";
import { formatInZone } from "../../lib/zones";
import { RUN_LABELS, outcomeLine, outcomeText } from "./reportView";
import { DayChip } from "./vizBits";

const RUN_CHIP = { sent: "green", partial: "amber", failed: "red", sending: "info" } as const;

/**
 * Past sends, folded below the report: the last one is in the header already. Newest
 * first, each with how every destination fared, by name, a failure in red, and who
 * sent it. Each time is read in the report's own zone (`timezone`, the schedule's), as
 * the header's last send is, and names it. The list is drawn only once it is opened.
 */
export function RunHistory({ reportId, timezone }: { reportId: string; timezone: string }) {
  const runs = useQuery({
    queryKey: ["day-reports", "runs", reportId],
    queryFn: () => apiClient.dayReportRuns(reportId),
  });
  const list = (runs.data ?? []).slice(0, 14);
  const count = runs.isSuccess ? ` · ${runs.data.length}` : "";

  return (
    <Fold summary={`Past sends${count}`}>
      <PanelState
        isLoading={runs.isLoading}
        error={runs.error}
        onRetry={() => void runs.refetch()}
        isEmpty={list.length === 0}
        emptyText="Nothing sent yet. A scheduled send goes out once per local day."
      >
        <ul className="grid gap-1.5 rounded-3xl bg-(--op-day-surface) text-(--op-day-ink)">
          {list.map((run) => (
            <li
              key={run.run_id}
              className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-[14px] border border-(--op-day-grid) px-3.5 py-2.5 text-[13px] text-(--op-day-body)"
            >
              <span className="font-extrabold tabular-nums text-(--op-day-ink)">
                {formatInZone(run.started_at, timezone)}
              </span>
              <span>
                {run.trigger === "manual"
                  ? `Send now · ${run.actor_name ?? run.actor ?? "someone"}`
                  : "Scheduled"}
              </span>
              <DayChip tone={RUN_CHIP[run.status]} small>
                {RUN_LABELS[run.status]}
              </DayChip>
              <span>{outcomeLine(run)}</span>
              <ul className="grid flex-[1_1_100%] gap-0.5 text-[12px]">
                {run.outcomes.map((outcome) => (
                  <li
                    key={`${outcome.kind}-${outcome.target}-${outcome.label}`}
                    className={
                      outcome.ok ? "text-(--op-day-secondary)" : "font-bold text-(--op-day-red)"
                    }
                  >
                    {outcomeText(outcome)}
                  </li>
                ))}
              </ul>
            </li>
          ))}
        </ul>
      </PanelState>
    </Fold>
  );
}
