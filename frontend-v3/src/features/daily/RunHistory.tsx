import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { PanelState, SectionHeader, TableBox, td, th } from "../../components/PanelState";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay, formatTime } from "../../lib/format";
import { RUN_LABELS, RUN_TONES, outcomeLine, outcomeText } from "./reportView";

/** Past sends, newest first, with how every destination fared (each named) and who sent it. */
export function RunHistory({ reportId }: { reportId: string }) {
  const runs = useQuery({
    queryKey: ["day-reports", "runs", reportId],
    queryFn: () => apiClient.dayReportRuns(reportId),
  });

  return (
    <section>
      <SectionHeader title="Past sends" />
      <PanelState
        needs="anyone with a member record"
        isLoading={runs.isLoading}
        error={runs.error}
        isEmpty={(runs.data ?? []).length === 0}
        emptyText="Nothing sent yet. A scheduled send goes out once per local day."
      >
        <TableBox>
          <table className="w-full min-w-[640px] border-collapse">
            <thead>
              <tr>
                <th className={th}>Day</th>
                <th className={th}>Sent</th>
                <th className={th}>How</th>
                <th className={th}>Result</th>
                <th className={th}>Destinations</th>
              </tr>
            </thead>
            <tbody>
              {(runs.data ?? []).slice(0, 14).map((run) => (
                <tr key={run.run_id}>
                  <td className={td}>{formatDay(run.report_date)}</td>
                  <td className={`${td} tabular-nums`}>{formatTime(run.started_at)}</td>
                  <td className={td}>
                    {run.trigger === "manual"
                      ? `Send now · ${run.actor_name ?? run.actor ?? "someone"}`
                      : "Scheduled"}
                  </td>
                  <td className={td}>
                    <RagChip tone={RUN_TONES[run.status]} className="h-6 px-2.5 text-[12px]">
                      {RUN_LABELS[run.status]}
                    </RagChip>
                  </td>
                  <td className={td}>
                    {outcomeLine(run)}
                    {/* Every destination, by name, with what happened: a failure in red. */}
                    <ul className="mt-1 grid gap-0.5 text-[12px]">
                      {run.outcomes.map((outcome) => (
                        <li
                          key={`${outcome.kind}-${outcome.target}-${outcome.label}`}
                          className={outcome.ok ? "text-grey-body" : "font-bold text-rag-red"}
                        >
                          {outcomeText(outcome)}
                        </li>
                      ))}
                    </ul>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableBox>
      </PanelState>
    </section>
  );
}
