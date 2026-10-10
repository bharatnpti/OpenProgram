import type { UseQueryResult } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import type { DirectoryItemResponse, ProjectDeliveryResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { CompactDateStrip } from "../../components/ui/DateStrip";
import { Panel } from "../../components/ui/Bits";
import { verdictWeight } from "../../components/ui/dateStripWords";

/**
 * Every project of the program with its committed date, its forecast and its
 * verdict, one line each, worst first: the dates no portfolio Today showed. A
 * project opens in Delivery. `reads` are the projects' delivery reads, in the
 * same order, shared with the heat tiles' due dates.
 */
export function DeliveryDatesPanel({
  projects,
  reads,
  waiting,
}: {
  projects: DirectoryItemResponse[];
  reads: UseQueryResult<ProjectDeliveryResponse>[];
  /** The projects themselves are still being read: "no projects" is not known yet. */
  waiting: boolean;
}) {
  const { access } = useRole();
  const rows = projects.map((project, index) => ({ project, read: reads[index] }));
  const loading = waiting || rows.some((row) => row.read?.isLoading);
  const unread = rows.filter((row) => row.read?.error).map((row) => row.project.name);
  const ranked = rows
    .flatMap((row) =>
      row.read?.data ? [{ project: row.project, scope: row.read.data.project }] : [],
    )
    .sort(
      (a, b) =>
        verdictWeight(b.scope) - verdictWeight(a.scope) ||
        (a.scope.target ?? "9999").localeCompare(b.scope.target ?? "9999") ||
        a.project.name.localeCompare(b.project.name),
    );
  const to = (id: string) =>
    access.delivery.project
      ? `/delivery/project/${encodeURIComponent(id)}`
      : `/reports/${encodeURIComponent(id)}/overall`;

  return (
    <Panel title="Delivery dates" note="committed, forecast and verdict · worst first">
      <PanelState
        isLoading={loading}
        error={unread.length > 0 && unread.length === rows.length ? rows[0].read?.error : null}
        isEmpty={rows.length === 0}
        emptyText="This program has no projects yet."
      >
        <ul>
          {ranked.map(({ project, scope }) => (
            <li
              key={project.id}
              className="flex min-w-0 flex-wrap items-center justify-between gap-x-4 gap-y-1 border-t border-grey-border py-3 first:border-t-0"
            >
              <Link
                to={to(project.id)}
                className="min-w-0 text-[14px] font-bold text-ink no-underline hover:underline"
              >
                {project.name}
              </Link>
              <CompactDateStrip scope={scope} />
            </li>
          ))}
        </ul>
        {unread.length > 0 && unread.length < rows.length ? (
          <p className="mt-2 text-[12px] text-grey-secondary">
            The dates of {unread.join(", ")} could not be read.
          </p>
        ) : null}
      </PanelState>
    </Panel>
  );
}
