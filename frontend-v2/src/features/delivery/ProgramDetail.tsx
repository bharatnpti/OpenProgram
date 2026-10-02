import { useState } from "react";
import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { RagChip } from "../../components/ui/RagChip";
import type {
  DirectoryItemResponse,
  ProgramTreeResponse,
  Rag,
  RollupFactorDto,
} from "../../api/schema";
import { useRole } from "../../app/role";
import { ragSeverity, toneForRag, toneHex } from "../../lib/status";
import type { DeliveryKind } from "../../lib/useDeliverySelection";
import { cn } from "../../lib/utils";

type TreeNode = ProgramTreeResponse["nodes"][number];

/** Factors that say the same thing, gathered so each reason is one line. */
type Reason = {
  key: string;
  description: string;
  contributes: Rag;
  sources: { kind: string; name: string }[];
};

/** Reasons shown before the list is expanded. */
const REASONS_SHOWN = 5;

/**
 * The program panel: how the program is doing, and why.
 *
 * It used to print only counts, so an exec drilling down from Today learned
 * less here than the heat row had already told them. Now it names what sets
 * the program's status, lists its projects worst first, and -- for the roles
 * that may read rollups -- shows the rollup reasons behind each status.
 */
export function ProgramDetail({
  program,
  asOf,
  onSelect,
}: {
  program: DirectoryItemResponse;
  asOf: string;
  onSelect: (kind: DeliveryKind, id: string) => void;
}) {
  // `canAccessPortfolio` is admin, manager or exec: the set holding
  // READ_PROGRAM_ROLLUP, which the program tree needs. Every other role still
  // gets the status of everything under the program from the directory.
  const { canAccessPortfolio: canReadRollup } = useRole();
  const [showAllReasons, setShowAllReasons] = useState(false);

  // Same keys as DeliveryPage, so nothing is fetched twice. Read here for
  // their loading and error states: an empty list while projects are still on
  // the way would otherwise read as "nothing rolls up into this program".
  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });
  const workstreams = useQuery({
    queryKey: ["directory", "workstreams", asOf],
    queryFn: () => apiClient.workstreams(asOf),
  });
  const pods = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
  });
  const tree = useQuery({
    queryKey: ["persona", "program-tree", program.id, asOf],
    queryFn: () => apiClient.personaProgramTree(program.id, asOf),
    enabled: canReadRollup,
  });

  const directory = [projects, workstreams, pods];
  const directoryFailed = directory.some((query) => query.isError && !query.data);
  const directoryLoading = !directoryFailed && directory.some((query) => !query.data);

  // The tree carries the same stored rollup the directory does, and computes
  // it when nothing is stored yet. Prefer it where it was read, so the
  // statuses on this panel always agree with the reasons beside them.
  const treeNodes = new Map((tree.data?.nodes ?? []).map((node) => [node.id, node]));
  const ragOf = (item: DirectoryItemResponse): Rag =>
    treeNodes.get(item.id)?.rag ?? item.rag ?? "unknown";

  const rag = ragOf(program);
  const programProjects = rankWorstFirst(
    (projects.data ?? []).filter((item) => program.project_ids.includes(item.id)),
    ragOf,
  );
  // A program usually holds projects only, but a workstream or pod linked to it
  // directly sets its status too, so it can be the reason named.
  const children = [
    ...programProjects,
    ...(workstreams.data ?? []).filter((item) => program.workstream_ids.includes(item.id)),
    ...(pods.data ?? []).filter((item) => program.pod_ids.includes(item.id)),
  ];

  const reason = directoryFailed
    ? "The projects under this program could not be loaded, so its status is not explained here."
    : directoryLoading
      ? "Loading the projects under this program…"
      : children.length === 0
        ? "No projects roll up into this program yet."
        : programReason(rag, children, ragOf);
  const counts =
    directoryFailed || directoryLoading ? null : rollupCounts(program, projects.data ?? []);

  const rootReasons = reasonsFor(treeNodes.get(program.id), treeNodes);
  const visibleReasons = showAllReasons ? rootReasons : rootReasons.slice(0, REASONS_SHOWN);
  const hiddenReasons = rootReasons.length - REASONS_SHOWN;

  return (
    <div className="animate-op-fade-up flex flex-col gap-6">
      <Card padding="p-7">
        <div className="flex items-center gap-3">
          <h2 className="text-[28px] font-extrabold">{program.name}</h2>
          <RagChip tone={toneForRag(rag)}>{rag}</RagChip>
        </div>
        <p className="mt-1.5 text-[15px] text-grey-secondary">program · {program.id}</p>
        <p className="mt-3 text-[16px] text-grey-body">{reason}</p>
        {counts ? (
          <p className="mt-1.5 text-[14px] text-grey-secondary">
            {plural(counts.projects, "project")} · {plural(counts.workstreams, "workstream")} ·{" "}
            {plural(counts.pods, "pod")} roll up into this program.
          </p>
        ) : null}
      </Card>

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1.4fr_1fr]">
        <Card padding="p-0" className={cn(!canReadRollup && "lg:col-span-2")}>
          <div className="flex items-baseline justify-between gap-4 px-6 pt-6 pb-2">
            <h3 className="text-[18px] font-bold">Projects</h3>
            {programProjects.length > 0 ? (
              <span className="text-[13px] font-bold text-grey-secondary">
                {tally(programProjects.map(ragOf))}
              </span>
            ) : null}
          </div>
          {directoryFailed ? (
            <p className="px-6 pb-6 text-[14px] text-grey-secondary">
              Projects could not be loaded.
            </p>
          ) : directoryLoading ? (
            <p className="px-6 pb-6 text-[14px] text-grey-secondary">Loading projects…</p>
          ) : programProjects.length === 0 ? (
            <p className="px-6 pb-6 text-[14px] text-grey-secondary">
              No projects roll up into this program yet.
            </p>
          ) : (
            programProjects.map((project) => {
              const projectRag = ragOf(project);
              const related = [
                ...rankWorstFirst(
                  (workstreams.data ?? []).filter((item) =>
                    project.workstream_ids.includes(item.id),
                  ),
                  ragOf,
                ).map((item) => ({ kind: "workstream" as const, item })),
                ...rankWorstFirst(
                  (pods.data ?? []).filter((item) => project.pod_ids.includes(item.id)),
                  ragOf,
                ).map((item) => ({ kind: "pod" as const, item })),
              ];
              // A green row needs no reason; any other one says what set it.
              const projectReasons =
                projectRag === "green" ? [] : reasonsFor(treeNodes.get(project.id), treeNodes);
              return (
                <div key={project.id} className="border-t border-grey-fill px-6 py-4">
                  <div className="flex items-start justify-between gap-4">
                    <button
                      type="button"
                      onClick={() => onSelect("project", project.id)}
                      className="group min-w-0 flex-1 text-left"
                    >
                      <div className="truncate text-[15px] font-bold group-hover:underline">
                        {project.name}
                      </div>
                      {projectReasons.length > 0 ? (
                        <div className="mt-0.5 text-[13px] text-grey-secondary">
                          {projectReasons[0].description}
                          {projectReasons.length > 1 ? ` · ${projectReasons.length - 1} more` : ""}
                        </div>
                      ) : null}
                    </button>
                    <RagChip tone={toneForRag(projectRag)}>{projectRag}</RagChip>
                  </div>
                  {related.length > 0 ? (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {related.map(({ kind, item }) => (
                        <button
                          key={`${kind}-${item.id}`}
                          type="button"
                          onClick={() => onSelect(kind, item.id)}
                          className="flex h-8 items-center gap-2 rounded-full border border-grey-border px-3 text-[13px] font-bold hover:border-ink"
                        >
                          <span
                            className="inline-block h-2 w-2 rounded-full"
                            style={{ backgroundColor: toneHex[toneForRag(ragOf(item))] }}
                          />
                          {item.name}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })
          )}
          {canReadRollup ? null : (
            <p className="border-t border-grey-fill px-6 py-4 text-sm text-grey-secondary">
              The reasons behind each status need a manager or executive role.
            </p>
          )}
        </Card>

        {canReadRollup ? (
          <Card variant="grey" padding="p-6">
            <h3 className="text-[18px] font-bold">{whyTitle(rag)}</h3>
            {tree.isError && !tree.data ? (
              <p className="mt-3 text-sm text-grey-secondary">
                The reasons behind this status could not be loaded.
              </p>
            ) : !tree.data ? (
              <p className="mt-3 text-sm text-grey-secondary">Loading the reasons…</p>
            ) : rootReasons.length === 0 ? (
              <p className="mt-3 text-sm text-grey-secondary">
                No reasons are recorded for this program yet.
              </p>
            ) : (
              <ul className="mt-3 flex flex-col gap-3">
                {visibleReasons.map((reason) => (
                  <li key={reason.key} className="flex gap-3">
                    <span
                      className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full"
                      style={{ backgroundColor: toneHex[toneForRag(reason.contributes)] }}
                    />
                    <div className="min-w-0">
                      <div className="text-[14px] font-bold">{reason.description}</div>
                      <div className="mt-0.5 text-[13px] text-grey-secondary">
                        {sourcesLabel(reason.sources)}
                      </div>
                    </div>
                  </li>
                ))}
              </ul>
            )}
            {tree.data && hiddenReasons > 0 ? (
              <button
                type="button"
                onClick={() => setShowAllReasons((value) => !value)}
                className="mt-4 text-[14px] font-bold text-magenta"
              >
                {showAllReasons ? "Show fewer" : `Show ${hiddenReasons} more`}
              </button>
            ) : null}
          </Card>
        ) : null}
      </div>
    </div>
  );
}

function rankWorstFirst(
  items: DirectoryItemResponse[],
  ragOf: (item: DirectoryItemResponse) => Rag,
): DirectoryItemResponse[] {
  return [...items].sort(
    (a, b) => ragSeverity(ragOf(b)) - ragSeverity(ragOf(a)) || a.name.localeCompare(b.name),
  );
}

/**
 * One sentence naming what sets the program's status. A program takes the
 * worst status beneath it, so the children that share its status are the
 * reason. Silence is never green: an unreported child is named as such.
 */
function programReason(
  rag: Rag,
  children: DirectoryItemResponse[],
  ragOf: (item: DirectoryItemResponse) => Rag,
): string {
  const matching = children.filter((item) => ragOf(item) === rag).map((item) => item.name);
  const verb = matching.length === 1 ? "is" : "are";
  if (rag === "red" || rag === "amber") {
    return matching.length > 0
      ? `${joinNames(matching)} ${verb} ${rag}, so the program is ${rag}.`
      : `Nothing under this program is ${rag} on its own; together it adds up to ${rag}.`;
  }
  if (rag === "green") {
    return matching.length === children.length
      ? "Everything under this program is green."
      : "Status is on track based on the latest confirmed or inferred signal.";
  }
  if (matching.length === children.length) {
    return "Nothing under this program has reported a status yet.";
  }
  if (matching.length > 0) {
    return `${joinNames(matching)} ${matching.length === 1 ? "has" : "have"} no status yet, so the program has none either.`;
  }
  return "No status is recorded for this program yet.";
}

/**
 * A node's rollup reasons, worst first, green ones only when nothing is worse.
 * The rollup keeps one factor per source, so six people with no status are six
 * factors; they read as one reason naming the six.
 */
function reasonsFor(node: TreeNode | undefined, nodes: Map<string, TreeNode>): Reason[] {
  const factors = node?.factors ?? [];
  const notGreen = factors.filter((factor) => factor.contributes !== "green");
  const ranked = [...(notGreen.length > 0 ? notGreen : factors)].sort(
    (a, b) => ragSeverity(b.contributes) - ragSeverity(a.contributes),
  );
  const reasons = new Map<string, Reason>();
  ranked.forEach((factor: RollupFactorDto) => {
    const key = `${factor.contributes}:${factor.description}`;
    const source = {
      kind: factor.source_ref.kind.replace(/_/g, " "),
      name: nodes.get(factor.source_ref.id)?.name ?? factor.source_ref.id,
    };
    const reason = reasons.get(key);
    if (reason) {
      reason.sources.push(source);
    } else {
      reasons.set(key, {
        key,
        description: factor.description,
        contributes: factor.contributes,
        sources: [source],
      });
    }
  });
  return [...reasons.values()];
}

function sourcesLabel(sources: Reason["sources"]): string {
  const names = joinNames(sources.map((source) => source.name));
  const kinds = new Set(sources.map((source) => source.kind));
  if (kinds.size > 1) return names;
  const kind = sources[0]?.kind ?? "";
  return sources.length === 1 ? `${kind} · ${names}` : `${sources.length} ${kind}s · ${names}`;
}

function whyTitle(rag: Rag): string {
  return rag === "unknown" ? "Why it has no status" : `Why it's ${rag}`;
}

function joinNames(names: string[]): string {
  if (names.length <= 2) return names.join(" and ");
  return `${names.slice(0, 2).join(", ")} and ${names.length - 2} more`;
}

function tally(rags: Rag[]): string {
  const order: [Rag, string][] = [
    ["red", "red"],
    ["amber", "amber"],
    ["unknown", "no status"],
    ["green", "green"],
  ];
  return order
    .map(([rag, label]) => {
      const count = rags.filter((item) => item === rag).length;
      return count > 0 ? `${count} ${label}` : null;
    })
    .filter((part) => part !== null)
    .join(" · ");
}

function plural(count: number, noun: string): string {
  return `${count} ${count === 1 ? noun : `${noun}s`}`;
}

function rollupCounts(
  item: DirectoryItemResponse,
  projects: DirectoryItemResponse[],
): { projects: number; workstreams: number; pods: number } {
  const owned = projects.filter((project) => item.project_ids.includes(project.id));
  const workstreams = new Set<string>(item.workstream_ids);
  const pods = new Set<string>(item.pod_ids);
  owned.forEach((project) => {
    project.workstream_ids.forEach((id) => workstreams.add(id));
    project.pod_ids.forEach((id) => pods.add(id));
  });
  return { projects: item.project_ids.length, workstreams: workstreams.size, pods: pods.size };
}
