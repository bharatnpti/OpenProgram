import { Link } from "react-router-dom";

import type { DirectoryItemResponse, TimelineResponse } from "../../api/schema";
import { projectTabPath } from "../../app/links";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { Card } from "../../components/ui/Card";
import { readState } from "../../lib/readState";
import { usePodTimeline, useTimeline } from "../overall/queries";
import { TimelineChart } from "../overall/TimelineChart";

/*
 * The timelines on Delivery's panels: the same rows and changes as Overall's
 * Timeline (labels above the bars, a shorter change list), without Why the
 * date moved. The project panel's lists the project and its pods (a scrum
 * master's, the pods only), where the date history used to be: the committed
 * date's moves are in What changed and the table. A pod's panel shows the pod's
 * own row. "Why it moved" opens Overall's Timeline with the row picked, for
 * the readers who have it there (not a developer).
 */

/** The project's rows: the project (for its readers) and its pods, worst first. */
export function ProjectTimeline({ projectId, readable }: { projectId: string; readable: boolean }) {
  const timeline = useTimeline(projectId, undefined, readable);
  if (!readable) return null;
  return (
    <CompactCard
      read={timeline.query}
      title="Timeline"
      label="Project timeline"
      projectId={projectId}
    />
  );
}

/** A pod's own row, for each project it works on. */
export function PodTimeline({ pod, readable }: { pod: DirectoryItemResponse; readable: boolean }) {
  const timeline = usePodTimeline(pod.id, readable);
  if (!readable) return null;
  return (
    <CompactCard
      read={timeline.query}
      title={`${pod.name}'s timeline`}
      label={`${pod.name}'s timeline`}
      projectId={timeline.query.data?.rows[0]?.project_id ?? ""}
    />
  );
}

function CompactCard({
  read,
  title,
  label,
  projectId,
}: {
  read: {
    data: TimelineResponse | undefined;
    isLoading: boolean;
    isPending: boolean;
    error: unknown;
    refetch: () => unknown;
  };
  title: string;
  label: string;
  projectId: string;
}) {
  const { access } = useRole();
  const data = read.data;
  // Overall explains a row its reader has whole; a developer has no Timeline there.
  const explained = data?.rows.find((row) => row.access === "panel");
  return (
    <PanelState
      {...readState(read)}
      onRetry={() => void read.refetch()}
      isEmpty={data !== undefined && data.rows.length === 0}
      emptyText="Nothing to draw yet: no row here has dates to show."
    >
      {data ? (
        <Card padding="p-5" className="op-viz-card grid min-w-0 gap-2 bg-(--op-viz-surface)">
          <TimelineChart
            data={data}
            compact
            label={label}
            heading={
              <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
                <h2 className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
                  {title}
                </h2>
                {access.overall.timeline && explained && projectId ? (
                  <Link
                    to={`${projectTabPath(projectId, "date")}?row=${encodeURIComponent(explained.row)}#timeline`}
                    className="text-[13px] font-bold text-ink underline underline-offset-2"
                  >
                    Why it moved
                  </Link>
                ) : null}
              </div>
            }
          />
        </Card>
      ) : null}
    </PanelState>
  );
}
