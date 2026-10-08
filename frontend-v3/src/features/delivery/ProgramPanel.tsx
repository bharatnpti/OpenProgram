import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse, PersonaTreeNodeDto } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { Panel, RagBadge, RagDot } from "../../components/ui/Bits";
import { readState } from "../../lib/readState";
import { ragSeverity } from "../../lib/status";
import { plural } from "../../lib/words";
import { ProjectCompactStrip } from "./DeliveryStrips";
import { FactorsPanel, NodeHeader } from "./NodeBits";
import { setBy } from "./factors";

/**
 * A program: what sets its status, its projects worst first with each one's
 * top reason and what sits under it, and a count of what rolls up into it.
 * The reasons need a manager, executive or admin.
 */
export function ProgramPanel({ program }: { program: DirectoryItemResponse }) {
  const { canReadPortfolio } = useRole();
  const tree = useQuery({
    queryKey: ["program", program.id, "tree"],
    queryFn: () => apiClient.personaProgramTree(program.id),
    enabled: canReadPortfolio,
  });

  const nodes = tree.data?.nodes ?? [];
  const edges = tree.data?.edges ?? [];
  const byId = new Map(nodes.map((n) => [n.id, n]));
  // The tree names every node a reason can come from: people, tasks, work items, repos.
  const names = Object.fromEntries(nodes.map((n) => [n.id, n.name]));
  const root = byId.get(program.id) ?? nodes.find((n) => n.kind === "program");
  const children = (id: string) =>
    edges
      .filter((e) => e.from_node_id === id)
      .map((e) => byId.get(e.to_node_id))
      .filter((n): n is PersonaTreeNodeDto => Boolean(n));
  const projects = children(root?.id ?? program.id)
    .filter((n) => n.kind === "project")
    .sort((a, b) => ragSeverity(b.rag) - ragSeverity(a.rag));
  const count = (kind: string) => nodes.filter((n) => n.kind === kind).length;
  // A red that no single reason makes (two different blockers) says so, not the worst amber one.
  const rootBy = root ? setBy(root.rag, root.factors) : null;

  return (
    <>
      <NodeHeader
        kind="Program"
        name={program.name}
        rag={root?.rag ?? program.rag}
        reason={rootBy ? `Set by: ${rootBy.text}` : undefined}
        read={readState(tree)}
      />
      <PanelState isLoading={tree.isLoading} error={tree.error}>
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
          <div className="flex flex-wrap gap-2 text-[13px] text-grey-body">
            {(["project", "workstream", "pod", "developer", "task"] as const)
              // Workstreams are optional: a program that uses none says nothing about them.
              .filter((kind) => kind !== "workstream" || count(kind) > 0)
              .map((kind) => (
                <span key={kind} className="rounded-full bg-grey-fill px-3 py-1 font-bold">
                  {kind === "developer"
                    ? plural(count(kind), "person", "people")
                    : plural(count(kind), kind, `${kind}s`)}
                </span>
              ))}
          </div>
          <Panel title="Projects" note="worst first">
            {projects.length === 0 ? (
              <p className="text-[14px] text-grey-secondary">
                No projects are linked to this program.
              </p>
            ) : (
              <ul className="grid gap-3">
                {projects.map((project) => {
                  const top = setBy(project.rag, project.factors);
                  const under = children(project.id).filter(
                    (n) => n.kind === "workstream" || n.kind === "pod",
                  );
                  return (
                    <li key={project.id} className="rounded-2xl border border-grey-border p-4">
                      <div className="flex flex-wrap items-center gap-2">
                        <RagDot rag={project.rag} />
                        <Link
                          to={`/delivery/project/${project.id}`}
                          className="text-[15px] font-extrabold text-ink no-underline hover:underline"
                        >
                          {project.name}
                        </Link>
                        <RagBadge rag={project.rag} />
                      </div>
                      <ProjectCompactStrip projectId={project.id} />
                      <p className="mt-1 text-[13px] text-grey-body">
                        {top ? top.text : "Nothing recorded for it yet."}
                      </p>
                      {under.length > 0 ? (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                          {under.map((n) => (
                            <Link
                              key={n.id}
                              to={`/delivery/${n.kind}/${n.id}`}
                              className="inline-flex items-center gap-1.5 rounded-full border border-grey-border px-2.5 py-0.5 text-[12px] font-bold text-ink no-underline hover:bg-grey-fill"
                            >
                              <RagDot rag={n.rag} className="h-2 w-2" />
                              {n.name}
                            </Link>
                          ))}
                        </div>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </Panel>
          {root ? <FactorsPanel factors={root.factors} names={names} /> : null}
        </div>
      </PanelState>
    </>
  );
}
