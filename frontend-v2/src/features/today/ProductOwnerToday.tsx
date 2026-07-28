import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { ProgressRing } from "../../components/ui/ProgressRing";
import { RagChip } from "../../components/ui/RagChip";
import { SegmentedBar } from "../../components/ui/SegmentedBar";
import { firstItemId } from "../../lib/selection";
import { toneForRag, toneHex } from "../../lib/status";
import { todayIso } from "../../lib/today";

export function ProductOwnerToday() {
  const asOf = todayIso();

  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });
  const projectId = firstItemId(projects.data ?? []);

  const progress = useQuery({
    queryKey: ["persona", "progress", projectId, asOf],
    queryFn: () => apiClient.projectProgress(projectId, asOf),
    enabled: Boolean(projectId),
  });

  const atRisk = (progress.data?.tasks ?? []).filter((task) => task.rag !== "green");

  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1fr_1.4fr]">
      <Card variant="grey" padding="p-7" animateDelay={70}>
        <h2 className="text-[20px] font-bold">{progress.data?.project_name ?? "Project"}</h2>
        {progress.data ? (
          <>
            <div className="mt-5 flex justify-center">
              <ProgressRing
                percent={progress.data.percent_complete}
                color={toneHex[toneForRag(progress.data.rag)]}
                size={120}
              />
            </div>
            <div className="mt-6">
              <SegmentedBar
                height={14}
                segments={[
                  { value: progress.data.green_tasks, color: "var(--op-green)", label: "On track" },
                  { value: progress.data.amber_tasks, color: "var(--op-amber)", label: "At risk" },
                  { value: progress.data.red_tasks, color: "var(--op-red)", label: "Blocked" },
                  { value: progress.data.unknown_tasks, color: "var(--op-unknown)", label: "Unknown" },
                ]}
              />
              <div className="mt-3 flex flex-wrap gap-4 text-[13px] text-grey-secondary">
                <LegendDot color="var(--op-green)" label={`${progress.data.green_tasks} on track`} />
                <LegendDot color="var(--op-amber)" label={`${progress.data.amber_tasks} at risk`} />
                <LegendDot color="var(--op-red)" label={`${progress.data.red_tasks} blocked`} />
                <LegendDot color="var(--op-unknown)" label={`${progress.data.unknown_tasks} unknown`} />
              </div>
            </div>
          </>
        ) : null}
      </Card>

      <Card padding="p-0" animateDelay={140}>
        <div className="px-6 pt-6 pb-2">
          <h2 className="text-[18px] font-bold">Needs your attention</h2>
        </div>
        {atRisk.map((task) => (
          <div
            key={task.id}
            className="flex items-center justify-between gap-4 border-t border-grey-fill px-6 py-4 first:border-t-0"
          >
            <div className="min-w-0">
              <div className="truncate text-[15px] font-bold">{task.name}</div>
              <div className="mt-0.5 text-[13px] text-grey-secondary">
                {task.id} · {task.source}
              </div>
            </div>
            <RagChip tone={toneForRag(task.rag)}>{task.rag}</RagChip>
          </div>
        ))}
        {progress.data && atRisk.length === 0 ? (
          <div className="px-6 py-6 text-sm text-grey-secondary">Everything is on track.</div>
        ) : null}
      </Card>
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="flex items-center gap-1.5">
      <span className="inline-block h-2 w-2 rounded-full" style={{ backgroundColor: color }} />
      {label}
    </span>
  );
}
