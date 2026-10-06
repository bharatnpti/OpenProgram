import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { ProgressRing } from "../../components/ui/ProgressRing";
import { RagChip } from "../../components/ui/RagChip";
import type { DirectoryItemResponse } from "../../api/schema";
import { toneForRag, toneHex } from "../../lib/status";
import type { DeliveryKind } from "../../lib/useDeliverySelection";
import { useRole } from "../../app/role";
import { cn } from "../../lib/utils";
import { PodTasks } from "./PodTasks";
import { ProgramDetail } from "./ProgramDetail";
import { DeliveryForecastCard } from "../forecast/DeliveryForecastCard";
import { PodDeliveryCard } from "../forecast/PodDeliveryCard";
import { ProjectGates } from "../gates/ProjectGates";
import { mayReadGateBoard } from "../gates/gates";
import { DayReportNoteCard } from "../reports/DayReportNoteCard";
import { RequirementsCard } from "../requirements/RequirementsCard";
import { RollupReasonsCard } from "./RollupReasonsCard";
import { leadReason, reasonsAddToLead } from "./rollupReasons";
import { useNodeReasons } from "./useNodeReasons";
import { TaskRow } from "./TaskRow";

type Lists = {
  programs: DirectoryItemResponse[];
  projects: DirectoryItemResponse[];
  workstreams: DirectoryItemResponse[];
  pods: DirectoryItemResponse[];
};

function findItem(kind: DeliveryKind, id: string, lists: Lists): DirectoryItemResponse | undefined {
  const list =
    kind === "program"
      ? lists.programs
      : kind === "project"
        ? lists.projects
        : kind === "workstream"
          ? lists.workstreams
          : lists.pods;
  return list.find((item) => item.id === id);
}

function relatedPills(item: DirectoryItemResponse | undefined, lists: Lists) {
  if (!item) return [];
  const pills: {
    kind: DeliveryKind;
    id: string;
    name: string;
    rag: DirectoryItemResponse["rag"];
  }[] = [];
  item.program_ids.forEach((id) => {
    const match = lists.programs.find((p) => p.id === id);
    if (match) pills.push({ kind: "program", id, name: match.name, rag: match.rag });
  });
  item.project_ids.forEach((id) => {
    const match = lists.projects.find((p) => p.id === id);
    if (match) pills.push({ kind: "project", id, name: match.name, rag: match.rag });
  });
  item.workstream_ids.forEach((id) => {
    const match = lists.workstreams.find((p) => p.id === id);
    if (match) pills.push({ kind: "workstream", id, name: match.name, rag: match.rag });
  });
  item.pod_ids.forEach((id) => {
    const match = lists.pods.find((p) => p.id === id);
    if (match) pills.push({ kind: "pod", id, name: match.name, rag: match.rag });
  });
  return pills;
}

export function DeliveryDetail({
  selection,
  lists,
  asOf,
  onSelect,
}: {
  selection: { kind: DeliveryKind; id: string };
  lists: Lists;
  asOf: string;
  onSelect: (kind: DeliveryKind, id: string) => void;
}) {
  const item = findItem(selection.kind, selection.id, lists);
  const { canReadProjectProgress, canReadPodDetail, canEditGates } = useRole();
  // The navigator lists every node, because the directory is readable by every
  // role. The detail behind a node is not: progress needs READ_PROJECT_PROGRESS
  // and a pod needs the pod capabilities. Asking anyway returned a 403 that the
  // panel rendered as em-dashes and, for a pod, a 0% ring -- a denial dressed up
  // as data.
  const mayReadDetail =
    selection.kind === "pod"
      ? canReadPodDetail
      : selection.kind === "program"
        ? true
        : canReadProjectProgress;
  // The gates have a reader of their own: a developer or scrum master signs
  // test cases off, so they read a project's gates and questions even where
  // its progress, forecast and requirements stay hidden from them.
  const mayReadGates =
    selection.kind === "project" && mayReadGateBoard({ canReadProjectProgress, canEditGates });

  const projectProgress = useQuery({
    queryKey: ["persona", "progress", selection.id, asOf],
    queryFn: () => apiClient.projectProgress(selection.id, asOf),
    enabled: selection.kind === "project" && Boolean(selection.id) && mayReadDetail,
  });
  const workstreamProgress = useQuery({
    queryKey: ["persona", "workstream-progress", selection.id, asOf],
    queryFn: () => apiClient.workstreamProgress(selection.id, asOf),
    enabled: selection.kind === "workstream" && Boolean(selection.id) && mayReadDetail,
  });
  const podCheckins = useQuery({
    queryKey: ["persona", "checkins", selection.id, asOf],
    queryFn: () => apiClient.podCheckins(selection.id, asOf),
    enabled: selection.kind === "pod" && Boolean(selection.id) && mayReadDetail,
  });
  const podBlockers = useQuery({
    queryKey: ["persona", "blockers", selection.id, asOf],
    queryFn: () => apiClient.podBlockers(selection.id, asOf),
    enabled: selection.kind === "pod" && Boolean(selection.id) && mayReadDetail,
  });
  const reasons = useNodeReasons({
    kind: selection.kind,
    id: selection.id,
    asOf,
    mayRead: mayReadDetail,
    progress: selection.kind === "project" ? projectProgress : workstreamProgress,
  });

  if (!item) {
    return (
      <Card padding="p-7">
        <p className="text-grey-secondary">Select a node from the navigator to see details.</p>
      </Card>
    );
  }

  if (selection.kind === "program") {
    return <ProgramDetail key={item.id} program={item} asOf={asOf} onSelect={onSelect} />;
  }

  const progress = selection.kind === "project" ? projectProgress.data : workstreamProgress.data;
  // The rag read beside the reasons, so the chip and its reasons agree.
  const rag = reasons.rag ?? item.rag;
  const tone = toneForRag(rag);
  // No ring at all rather than a 0% one: 0% confirmed and "not yours to read"
  // are very different things and must not look the same.
  const percent =
    selection.kind === "pod"
      ? podCheckins.data && podCheckins.data.developers.length > 0
        ? (podCheckins.data.confirmed / podCheckins.data.developers.length) * 100
        : null
      : (progress?.percent_complete ?? null);

  const tasks = progress?.tasks ?? [];
  // A pod's tasks come from their own read, under the pod capabilities.
  const showPodTasks = selection.kind === "pod" && mayReadDetail;
  const pills = relatedPills(item, lists);

  return (
    <div
      key={`${selection.kind}-${selection.id}`}
      className="animate-op-fade-up flex flex-col gap-6"
    >
      <Card padding="p-7">
        <div className="grid grid-cols-1 gap-6 sm:grid-cols-[1fr_132px]">
          <div>
            <div className="flex items-center gap-3">
              <h2 className="text-[28px] font-extrabold">{item.name}</h2>
              <RagChip tone={tone}>{rag ?? "unknown"}</RagChip>
            </div>
            <p className="mt-1.5 text-[15px] text-grey-secondary">
              {selection.kind} · {item.id}
            </p>
            <p className="mt-3 text-[16px] text-grey-body">
              {leadReason({
                noun: selection.kind,
                selfId: item.id,
                rag: rag ?? "unknown",
                state: reasons.state,
                deniedRole: deniedRoleFor(selection.kind),
              })}
            </p>
            {pills.length > 0 ? (
              <div className="mt-5 flex flex-wrap gap-2">
                {pills.map((pill) => (
                  <button
                    key={`${pill.kind}-${pill.id}`}
                    type="button"
                    onClick={() => onSelect(pill.kind, pill.id)}
                    className="flex h-9 items-center gap-2 rounded-full border border-grey-border px-3.5 text-[13px] font-bold hover:border-ink"
                  >
                    <span
                      className="inline-block h-2 w-2 rounded-full"
                      style={{ backgroundColor: toneHex[toneForRag(pill.rag)] }}
                    />
                    {pill.name}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          {percent !== null ? (
            <div className="flex justify-center sm:justify-end">
              <ProgressRing percent={percent} color={toneHex[tone]} size={132} />
            </div>
          ) : null}
        </div>
      </Card>

      {reasons.state.status === "ready" &&
      reasonsAddToLead(rag ?? "unknown", reasons.state.reasons) ? (
        <RollupReasonsCard rag={rag ?? "unknown"} noun={selection.kind} state={reasons.state} />
      ) : null}

      {selection.kind === "project" && mayReadDetail ? (
        <>
          <DeliveryForecastCard projectId={selection.id} asOf={asOf} />
          <DayReportNoteCard projectId={selection.id} />
          <RequirementsCard projectId={selection.id} asOf={asOf} />
        </>
      ) : null}

      {mayReadGates ? <ProjectGates projectId={selection.id} asOf={asOf} /> : null}

      {selection.kind === "pod" && mayReadDetail ? (
        <PodDeliveryCard podId={selection.id} asOf={asOf} />
      ) : null}

      <div className="grid grid-cols-1 items-start gap-6 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
        {showPodTasks ? (
          <PodTasks podId={selection.id} asOf={asOf} />
        ) : tasks.length > 0 ? (
          <Card padding="p-0">
            <div className="px-6 pt-6 pb-2">
              <h3 className="text-[18px] font-bold">Tasks</h3>
            </div>
            {tasks.map((task) => (
              <TaskRow key={task.id} task={task} />
            ))}
          </Card>
        ) : null}

        <Card
          variant="grey"
          padding="p-6"
          className={cn(tasks.length === 0 && !showPodTasks && "lg:col-span-2")}
        >
          <h3 className="text-[18px] font-bold">Details</h3>
          {mayReadDetail ? null : (
            <p className="mt-3 text-sm text-grey-secondary">{detailDeniedNote(selection.kind)}</p>
          )}
          <dl className="mt-3 flex flex-col gap-2.5">
            {detailRows(selection.kind, item, lists, {
              progress,
              checkins: podCheckins.data,
              blockers: podBlockers.data,
            }).map(([label, value]) => (
              <div key={label} className="flex items-center justify-between gap-4 text-[14px]">
                <dt className="text-grey-secondary">{label}</dt>
                <dd className="font-bold">{value}</dd>
              </div>
            ))}
          </dl>
        </Card>
      </div>
    </div>
  );
}

function detailRows(
  kind: DeliveryKind,
  item: DirectoryItemResponse,
  lists: Lists,
  data: {
    progress?:
      | {
          total_tasks: number;
          green_tasks: number;
          amber_tasks: number;
          red_tasks: number;
          unknown_tasks: number;
          confidence: number | null;
          source: string;
        }
      | undefined;
    checkins: { confirmed: number; developers: unknown[] } | undefined;
    blockers: { blockers: unknown[] } | undefined;
  },
): [string, string][] {
  if (kind === "pod") {
    return [
      ["Members", String(item.member_ids.length)],
      [
        "Confirmed",
        data.checkins ? `${data.checkins.confirmed} of ${data.checkins.developers.length}` : "—",
      ],
      ["Open blockers", data.blockers ? String(data.blockers.blockers.length) : "—"],
    ];
  }
  if (kind === "workstream") {
    const metadata = item.metadata;
    return [
      ["Type", stringMeta(metadata.type)],
      ["Phase", stringMeta(metadata.phase)],
      ["Owner", personMeta(item, "owner_id")],
      ["TPM", personMeta(item, "tpm_id")],
      ["SM", personMeta(item, "sm_id")],
      ["Target date", stringMeta(metadata.target_date)],
    ];
  }
  return [
    ["Code", item.code ?? "—"],
    ["Source", data.progress?.source ?? item.source ?? "—"],
    [
      "Confidence",
      data.progress?.confidence != null ? `${Math.round(data.progress.confidence * 100)}%` : "—",
    ],
    ["Total tasks", data.progress ? String(data.progress.total_tasks) : "—"],
    [
      "On track / at risk / blocked",
      data.progress
        ? `${data.progress.green_tasks} / ${data.progress.amber_tasks} / ${data.progress.red_tasks}`
        : "—",
    ],
  ];
}

function stringMeta(value: unknown): string {
  return typeof value === "string" && value ? value : "—";
}

/**
 * A person field by name. The stored value is an id, and the member list is
 * admin-only, so the directory resolves it for every role. An id that matches
 * no member is shown as an id, never passed off as a name.
 */
function personMeta(item: DirectoryItemResponse, key: string): string {
  const person = item.people.find((entry) => entry.key === key);
  if (!person) return stringMeta(item.metadata[key]);
  return person.name ?? `${person.id} · not a member`;
}

/** Which role opens this kind of node, for the panel to say so plainly. */
function detailDeniedNote(kind: DeliveryKind): string {
  if (kind === "pod") {
    return "Pod check-ins and blockers need a scrum-master or manager role.";
  }
  return `${kind === "project" ? "Project" : "Workstream"} progress needs a product-owner, manager or executive role.`;
}

/** The role that reads why this kind of node has its colour. */
function deniedRoleFor(kind: DeliveryKind): string {
  return kind === "pod"
    ? "a scrum-master or manager role"
    : "a product-owner, manager or executive role";
}
