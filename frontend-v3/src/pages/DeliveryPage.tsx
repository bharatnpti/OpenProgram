import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Navigate, NavLink, useParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type { DirectoryItemResponse } from "../api/schema";
import { useOwnTree, usePods, usePrograms, useProjects, useWorkstreams } from "../app/directory";
import { useRole } from "../app/role";
import { useDayWords } from "../app/viewingDate";
import { PanelState } from "../components/PanelState";
import { RagDot } from "../components/ui/Bits";
import { Trail, type OwnPanel } from "../features/delivery/NodeBits";
import { PodPanel } from "../features/delivery/PodPanel";
import { ProgramPanel } from "../features/delivery/ProgramPanel";
import { ProjectNamesPanel } from "../features/delivery/ProjectNamesPanel";
import { ProjectPanel } from "../features/delivery/ProjectPanel";
import { WorkstreamPanel } from "../features/delivery/WorkstreamPanel";
import { useAssistantSubject } from "../features/assistant/assistantContext";
import type { Kind } from "../features/delivery/factors";
import {
  ownGroups,
  ownLanding,
  ownProjects,
  trailOf,
  type OwnNode,
} from "../features/delivery/ownTree";
import { ragSeverity, ragWords } from "../lib/status";
import { cn } from "../lib/utils";

/**
 * Walk the delivery graph. A manager, executive or admin walks all of it: the
 * navigator lists the programs, projects, workstreams (those that hold work)
 * and pods the role's Delivery offers (an executive's lists no pods). Everyone
 * else walks their own part (`own`, app/access.ts). The selection lives in the
 * URL (/delivery/:kind/:id) so any panel can be linked. Each panel says what
 * its colour is and why.
 */
export function DeliveryPage() {
  const { access } = useRole();
  return access.deliveryScope === "own" ? <OwnDelivery /> : <PortfolioDelivery />;
}

const navBox =
  "max-h-[50vh] overflow-y-auto rounded-3xl border border-grey-border p-3 lg:sticky lg:self-start lg:top-32 lg:max-h-[calc(100vh-10rem-var(--op-float-room))]";
const groupLabel = "px-2 pb-1 text-[11px] font-bold uppercase tracking-wider text-grey-secondary";
const navLink = ({ isActive }: { isActive: boolean }) =>
  cn(
    "flex items-center gap-2 rounded-xl px-2 py-1.5 text-[14px] no-underline",
    isActive ? "bg-ink font-bold text-white" : "text-ink hover:bg-grey-fill",
  );

/** A node's dot with its colour in words for a screen reader; nothing where none is read. */
function Dot({ rag, shown = true }: { rag: DirectoryItemResponse["rag"]; shown?: boolean }) {
  if (!shown) return <span aria-hidden className="h-2.5 w-2.5 flex-none" />;
  return <RagDot rag={rag} />;
}

function StatusWords({ rag }: { rag: DirectoryItemResponse["rag"] }) {
  return <span className="sr-only">, {ragWords(rag)}</span>;
}

function Layout({ nav, children }: { nav: ReactNode; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[280px_minmax(0,1fr)]">
      {nav}
      <div className="min-w-0">{children}</div>
    </div>
  );
}

function PortfolioDelivery() {
  const { kind, id } = useParams();
  const day = useDayWords();
  const { access } = useRole();
  const programs = usePrograms();
  const projects = useProjects();
  const workstreams = useWorkstreams();
  const pods = usePods();
  // The workstreams too: a link to one reads "not in the directory" until its list arrives.
  const loading =
    programs.isLoading || projects.isLoading || workstreams.isLoading || pods.isLoading;
  const error = programs.error ?? projects.error ?? pods.error ?? workstreams.error;

  // Only the kinds this role's Delivery lists (an executive's has no pods), and
  // only workstreams that hold work: they are optional.
  const groups: { kind: Kind; label: string; items: DirectoryItemResponse[] }[] = (
    [
      { kind: "program", label: "Programs", items: programs.data ?? [] },
      { kind: "project", label: "Projects", items: projects.data ?? [] },
      {
        kind: "workstream",
        label: "Workstreams",
        items: (workstreams.data ?? []).filter((item) => item.in_use !== false),
      },
      { kind: "pod", label: "Pods", items: pods.data ?? [] },
    ] as const
  ).filter((group) => access.delivery[group.kind]);
  const all = groups.flatMap((g) => g.items.map((item) => ({ kind: g.kind, item })));
  const selected = all.find((x) => x.kind === kind && x.item.id === id);
  useAssistantSubject(selected ? { kind: selected.kind, name: selected.item.name } : null);

  if (!loading && !kind && all.length > 0) {
    const first = all.find((x) => x.kind === "program") ?? all[0];
    return <Navigate to={`/delivery/${first.kind}/${first.item.id}`} replace />;
  }

  const find = (k: Kind, itemId: string) =>
    groups.find((g) => g.kind === k)?.items.find((item) => item.id === itemId);

  return (
    <PanelState
      isLoading={loading}
      error={error}
      isEmpty={all.length === 0}
      emptyText={
        day === "today"
          ? "Nothing is configured yet. An admin adds programs, projects and pods under Admin → Entities."
          : `Nothing was configured yet ${day}.`
      }
    >
      <Layout
        nav={
          <nav aria-label="Delivery graph" className={navBox}>
            {groups
              .filter((g) => g.items.length > 0)
              .map((g) => (
                <div key={g.kind} className="mb-3 last:mb-0">
                  <p className={groupLabel}>{g.label}</p>
                  <ul>
                    {[...g.items]
                      .sort(
                        (a, b) =>
                          ragSeverity(b.rag) - ragSeverity(a.rag) || a.name.localeCompare(b.name),
                      )
                      .map((item) => (
                        <li key={item.id}>
                          <NavLink to={`/delivery/${g.kind}/${item.id}`} className={navLink}>
                            <Dot rag={item.rag} />
                            <span className="min-w-0 truncate">{item.name}</span>
                            <StatusWords rag={item.rag} />
                          </NavLink>
                        </li>
                      ))}
                  </ul>
                </div>
              ))}
          </nav>
        }
      >
        {!selected ? (
          <NotListed kind={kind} id={id} day={day} />
        ) : selected.kind === "program" ? (
          <ProgramPanel program={selected.item} />
        ) : selected.kind === "project" ? (
          <ProjectPanel project={selected.item} find={find} />
        ) : selected.kind === "workstream" ? (
          <WorkstreamPanel workstream={selected.item} find={find} />
        ) : (
          <PodPanel pod={selected.item} find={find} />
        )}
      </Layout>
    </PanelState>
  );
}

/**
 * The person's own part of the tree (`GET /me/delivery-tree`): each of their
 * projects under its program's name, and under each project every pod. A pod
 * that opens is a link with its colour; one that does not (a developer's
 * neighbours) is a muted name. A project whose data is not theirs (a
 * developer's) opens on its name and pods only. The server says what each
 * node opens on; the panels read the rest from the directory.
 */
function OwnDelivery() {
  const { kind, id } = useParams();
  const day = useDayWords();
  const tree = useOwnTree();
  const projects = useProjects();
  const pods = usePods();
  const loading = tree.isLoading || projects.isLoading || pods.isLoading;
  const error = tree.error ?? projects.error ?? pods.error;

  const groups = tree.data ? ownGroups(tree.data) : [];
  const listed = tree.data ? ownProjects(tree.data) : [];
  const project = kind === "project" ? listed.find((p) => p.id === id) : undefined;
  const podNode =
    kind === "pod" ? listed.flatMap((p) => p.pods).find((p) => p.id === id && p.opens) : undefined;
  const selectedName = project?.name ?? podNode?.name;
  useAssistantSubject(
    selectedName && (kind === "project" || kind === "pod") ? { kind, name: selectedName } : null,
  );

  if (!loading && !kind && tree.data) {
    const landing = ownLanding(tree.data);
    if (landing) return <Navigate to={landing} replace />;
  }

  // The links a panel shows: what the navigator lists and opens, as it colours them.
  const openPods = (projectId: string) =>
    listed.find((p) => p.id === projectId)?.pods.filter((pod) => pod.opens) ?? [];
  const podProjects = (podId: string) =>
    listed
      .filter((p) => p.pods.some((pod) => pod.id === podId))
      .map((p: OwnNode) => ({ ...p, noStatus: p.rag === null }));
  const trail = (k: "project" | "pod", nodeId: string) =>
    tree.data ? <Trail steps={trailOf(tree.data, k, nodeId)} /> : null;
  const find = (k: Kind, itemId: string) =>
    (k === "project" ? projects.data : k === "pod" ? pods.data : undefined)?.find(
      (item) => item.id === itemId,
    );

  let panel: ReactNode;
  if (project) {
    const item = find("project", project.id);
    const own: OwnPanel = {
      full: project.full,
      related: openPods(project.id),
      trail: trail("project", project.id),
    };
    panel =
      project.full && item ? (
        <ProjectPanel project={item} find={find} own={own} />
      ) : (
        <ProjectNamesPanel project={project} pods={project.pods} trail={own.trail} />
      );
  } else if (podNode && find("pod", podNode.id)) {
    const item = find("pod", podNode.id) as DirectoryItemResponse;
    panel = (
      <PodPanel
        pod={item}
        find={find}
        own={{ full: podNode.full, related: podProjects(podNode.id), trail: trail("pod", item.id) }}
      />
    );
  } else {
    // A link the guard left here: a node not on the day shown, or a list still settling.
    panel = (
      <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
        Pick a project or pod on the left.
      </p>
    );
  }

  return (
    <PanelState
      isLoading={loading}
      error={error}
      isEmpty={listed.length === 0}
      emptyText={
        day === "today"
          ? "None of your pods works on a project yet, so there is nothing here to show."
          : `None of your pods worked on a project ${day}.`
      }
    >
      <Layout
        nav={
          <nav aria-label="Your projects and pods" className={navBox}>
            {groups.map((group) => (
              <div key={group.program?.id ?? "-"} className="mb-3 last:mb-0">
                <p className={groupLabel}>{group.program?.name ?? "Projects"}</p>
                <ul>
                  {group.projects.map((p) => (
                    <li key={p.id} className="mb-1 last:mb-0">
                      <NavLink
                        to={`/delivery/project/${encodeURIComponent(p.id)}`}
                        className={navLink}
                      >
                        <Dot rag={p.rag} shown={p.rag !== null} />
                        <span className="min-w-0 truncate font-bold">{p.name}</span>
                        {p.rag !== null ? <StatusWords rag={p.rag} /> : null}
                      </NavLink>
                      <ul className="ml-3 border-l border-grey-border pl-2">
                        {p.pods.map((pod) => (
                          <li key={pod.id}>
                            {pod.opens ? (
                              <NavLink
                                to={`/delivery/pod/${encodeURIComponent(pod.id)}`}
                                className={navLink}
                              >
                                <Dot rag={pod.rag} />
                                <span className="min-w-0 truncate">{pod.name}</span>
                                <StatusWords rag={pod.rag} />
                              </NavLink>
                            ) : (
                              <span className="flex items-center gap-2 px-2 py-1.5 text-[14px] text-grey-secondary">
                                <Dot rag={null} shown={false} />
                                <span className="min-w-0 truncate">{pod.name}</span>
                              </span>
                            )}
                          </li>
                        ))}
                      </ul>
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </nav>
        }
      >
        {panel}
      </Layout>
    </PanelState>
  );
}

/**
 * A link to something the lists leave out. Workstreams are optional, so the
 * lists skip one that holds no work; reading it directly says whether that is
 * why, rather than calling it missing.
 */
function NotListed({ kind, id, day }: { kind?: string; id?: string; day: string }) {
  const direct = useQuery({
    queryKey: ["workstream", id, "direct"],
    queryFn: () => apiClient.workstream(id ?? ""),
    enabled: kind === "workstream" && Boolean(id),
    retry: false,
  });
  const empty = kind === "workstream" && direct.data?.in_use === false;
  return (
    <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
      {empty
        ? `${direct.data?.name} holds no tasks or work items ${day}, so Delivery leaves it out. Pods carry the work; a workstream shows once something is linked to it.`
        : `That ${kind ?? "item"} is not in the directory${day === "today" ? "" : ` ${day}`}. Pick one on the left.`}
    </p>
  );
}
