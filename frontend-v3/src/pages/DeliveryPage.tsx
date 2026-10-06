import { Navigate, NavLink, useParams } from "react-router-dom";

import type { DirectoryItemResponse } from "../api/schema";
import { usePods, usePrograms, useProjects, useWorkstreams } from "../app/directory";
import { PanelState } from "../components/PanelState";
import { RagDot } from "../components/ui/Bits";
import { PodPanel } from "../features/delivery/PodPanel";
import { ProgramPanel } from "../features/delivery/ProgramPanel";
import { ProjectPanel } from "../features/delivery/ProjectPanel";
import { WorkstreamPanel } from "../features/delivery/WorkstreamPanel";
import type { Kind } from "../features/delivery/factors";
import { ragSeverity } from "../lib/status";
import { cn } from "../lib/utils";

/**
 * Walk the delivery graph. The navigator lists every program, project,
 * workstream and pod; the selection lives in the URL (/delivery/:kind/:id) so
 * any panel can be linked. Each panel says what its colour is and why.
 */
export function DeliveryPage() {
  const { kind, id } = useParams();
  const programs = usePrograms();
  const projects = useProjects();
  const workstreams = useWorkstreams();
  const pods = usePods();
  const loading = programs.isLoading || projects.isLoading || pods.isLoading;
  const error = programs.error ?? projects.error ?? pods.error ?? workstreams.error;

  const groups: { kind: Kind; label: string; items: DirectoryItemResponse[] }[] = [
    { kind: "program", label: "Programs", items: programs.data ?? [] },
    { kind: "project", label: "Projects", items: projects.data ?? [] },
    { kind: "workstream", label: "Workstreams", items: workstreams.data ?? [] },
    { kind: "pod", label: "Pods", items: pods.data ?? [] },
  ];
  const all = groups.flatMap((g) => g.items.map((item) => ({ kind: g.kind, item })));

  if (!loading && !kind && all.length > 0) {
    const first = all.find((x) => x.kind === "program") ?? all[0];
    return <Navigate to={`/delivery/${first.kind}/${first.item.id}`} replace />;
  }

  const selected = all.find((x) => x.kind === kind && x.item.id === id);
  const find = (k: Kind, itemId: string) =>
    groups.find((g) => g.kind === k)?.items.find((item) => item.id === itemId);

  return (
    <PanelState
      needs="anyone with a member record"
      isLoading={loading}
      error={error}
      isEmpty={all.length === 0}
      emptyText="Nothing is configured yet. An admin adds programs, projects and pods under Admin → Entities."
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
        <nav
          aria-label="Delivery graph"
          className="max-h-[50vh] overflow-y-auto rounded-3xl border border-grey-border p-3 lg:sticky lg:self-start lg:top-32 lg:max-h-[calc(100vh-10rem)]"
        >
          {groups
            .filter((g) => g.items.length > 0)
            .map((g) => (
              <div key={g.kind} className="mb-3 last:mb-0">
                <p className="px-2 pb-1 text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
                  {g.label}
                </p>
                <ul>
                  {[...g.items]
                    .sort(
                      (a, b) =>
                        ragSeverity(b.rag) - ragSeverity(a.rag) || a.name.localeCompare(b.name),
                    )
                    .map((item) => (
                      <li key={item.id}>
                        <NavLink
                          to={`/delivery/${g.kind}/${item.id}`}
                          className={({ isActive }) =>
                            cn(
                              "flex items-center gap-2 rounded-xl px-2 py-1.5 text-[14px] no-underline",
                              isActive
                                ? "bg-ink font-bold text-white"
                                : "text-ink hover:bg-grey-fill",
                            )
                          }
                        >
                          <RagDot rag={item.rag} />
                          <span className="min-w-0 truncate">{item.name}</span>
                        </NavLink>
                      </li>
                    ))}
                </ul>
              </div>
            ))}
        </nav>
        <div className="min-w-0">
          {!selected ? (
            <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
              That {kind ?? "item"} is not in the directory. Pick one on the left.
            </p>
          ) : selected.kind === "program" ? (
            <ProgramPanel program={selected.item} />
          ) : selected.kind === "project" ? (
            <ProjectPanel project={selected.item} find={find} />
          ) : selected.kind === "workstream" ? (
            <WorkstreamPanel workstream={selected.item} find={find} />
          ) : (
            <PodPanel pod={selected.item} find={find} />
          )}
        </div>
      </div>
    </PanelState>
  );
}
