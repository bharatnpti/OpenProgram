import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { RagChip } from "../../components/ui/RagChip";
import { errorMessage } from "../admin/adminTypes";
import { runStartedBy } from "./reportForm";
import { RUN_LABELS, RUN_TONES, formatReportDay, reportRunsQueryKey } from "./reportView";

/**
 * Past sends, newest first: the day, who sent it (or "on schedule"), and how
 * each destination fared in the server's fixed sentences. A reader who may not
 * set the report up gets people by name and other places by kind only.
 */
export function ReportHistory({ reportId }: { reportId: string }) {
  const runs = useQuery({
    queryKey: reportRunsQueryKey(reportId),
    queryFn: () => apiClient.dayReportRuns(reportId),
  });
  if (runs.isError) {
    return <p className="text-[14px] text-rag-red">{errorMessage(runs.error)}</p>;
  }
  if (!runs.data) return <p className="text-[14px] text-grey-secondary">Loading…</p>;
  if (runs.data.length === 0) {
    return <p className="text-[14px] text-grey-secondary">It has not been sent yet.</p>;
  }
  return (
    <ul className="flex flex-col gap-3">
      {runs.data.map((run) => (
        <li key={run.run_id} className="rounded-2xl border border-grey-border p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[14px] font-bold">
              {formatReportDay(run.report_date)} · {runStartedBy(run)}
            </span>
            <RagChip tone={RUN_TONES[run.status]} dot className="h-6 text-[12px]">
              {RUN_LABELS[run.status]}
            </RagChip>
          </div>
          {run.outcomes.length > 0 ? (
            <ul className="mt-2 flex flex-col gap-1 text-[13px]">
              {run.outcomes.map((outcome, index) => (
                <li
                  key={index}
                  className={`break-words ${outcome.ok ? "text-grey-body" : "text-rag-red"}`}
                >
                  {outcome.label}: {outcome.detail}
                </li>
              ))}
            </ul>
          ) : null}
        </li>
      ))}
    </ul>
  );
}
