import type { ProjectProgressResponse, WorkstreamProgressResponse } from "../../api/schema";
import { TableBox, td, th } from "../../components/PanelState";
import { Panel, ProgressRing, RagBadge } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { ragSeverity } from "../../lib/status";
import { sourceLine } from "../../lib/words";

/** Progress for a project or workstream: ring, counts by RAG, source, and the task list. */
export function ProgressBlock({
  progress,
}: {
  progress: ProjectProgressResponse | WorkstreamProgressResponse;
}) {
  const tasks = [...progress.tasks].sort(
    (a, b) => ragSeverity(b.rag) - ragSeverity(a.rag) || a.name.localeCompare(b.name),
  );
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      <Panel title="Progress">
        <div className="flex flex-wrap items-center gap-5">
          <ProgressRing percent={progress.total_tasks > 0 ? progress.percent_complete : null} />
          <div className="grid gap-2">
            <div className="flex flex-wrap gap-1.5">
              <RagChip tone="success" className="h-6 px-2.5 text-[12px]">
                {progress.green_tasks} green
              </RagChip>
              <RagChip tone="warning" className="h-6 px-2.5 text-[12px]">
                {progress.amber_tasks} amber
              </RagChip>
              <RagChip tone="danger" className="h-6 px-2.5 text-[12px]">
                {progress.red_tasks} red
              </RagChip>
              <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                {progress.unknown_tasks} unknown
              </RagChip>
            </div>
            <p className="text-[12px] text-grey-secondary">
              {progress.total_tasks} tasks · {sourceLine(progress.source, progress.confidence)}
            </p>
          </div>
        </div>
      </Panel>
      {tasks.length > 0 ? (
        <TableBox>
          <table className="w-full min-w-[620px] border-collapse">
            <thead>
              <tr>
                <th className={th}>Task</th>
                <th className={th}>Status</th>
                <th className={th}>Source</th>
                <th className={th}>Tracker</th>
                <th className={th}>Due</th>
              </tr>
            </thead>
            <tbody>
              {tasks.map((task) => (
                <tr key={task.id}>
                  <td className={td}>
                    <span className="font-bold">{task.name}</span>
                    <span className="block text-[12px] text-grey-secondary">{task.id}</span>
                  </td>
                  <td className={td}>
                    <RagBadge rag={task.rag} />
                  </td>
                  <td className={td}>{sourceLine(task.source, task.confidence)}</td>
                  <td className={td}>{task.tracker_status ?? "—"}</td>
                  <td className={`${td} whitespace-nowrap`}>{formatDay(task.deadline)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableBox>
      ) : null}
    </div>
  );
}
