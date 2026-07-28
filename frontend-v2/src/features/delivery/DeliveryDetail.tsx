import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { ProgressRing } from "../../components/ui/ProgressRing";
import { RagChip } from "../../components/ui/RagChip";
import type { DirectoryItemResponse } from "../../api/schema";
import { toneForRag, toneHex } from "../../lib/status";
import type { DeliveryKind } from "../../lib/useDeliverySelection";
import { cn } from "../../lib/utils";

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
  const pills: { kind: DeliveryKind; id: string; name: string; rag: DirectoryItemResponse["rag"] }[] =
    [];
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

  const projectProgress = useQuery({
    queryKey: ["persona", "progress", selection.id, asOf],
    queryFn: () => apiClient.projectProgress(selection.id, asOf),
    enabled: selection.kind === "project" && Boolean(selection.id),
  });
  const workstreamProgress = useQuery({
    queryKey: ["persona", "workstream-progress", selection.id, asOf],
    queryFn: () => apiClient.workstreamProgress(selection.id, asOf),
    enabled: selection.kind === "workstream" && Boolean(selection.id),
  });
  const podCheckins = useQuery({
    queryKey: ["persona", "checkins", selection.id, asOf],
    queryFn: () => apiClient.podCheckins(selection.id, asOf),
    enabled: selection.kind === "pod" && Boolean(selection.id),
  });
  const podBlockers = useQuery({
    queryKey: ["persona", "blockers", selection.id, asOf],
    queryFn: () => apiClient.podBlockers(selection.id, asOf),
    enabled: selection.kind === "pod" && Boolean(selection.id),
  });

  if (!item) {
    return (
      <Card padding="p-7">
        <p className="text-grey-secondary">Select a node from the navigator to see details.</p>
      </Card>
    );
  }

  const progress = selection.kind === "project" ? projectProgress.data : workstreamProgress.data;
  const rag = selection.kind === "pod" ? item.rag : progress?.rag ?? item.rag;
  const tone = toneForRag(rag);
  const percent =
    selection.kind === "pod"
      ? podCheckins.data && podCheckins.data.developers.length > 0
        ? (podCheckins.data.confirmed / podCheckins.data.developers.length) * 100
        : 0
      : progress?.percent_complete ?? null;

  const tasks = progress?.tasks ?? [];
  const pills = relatedPills(item, lists);

  return (
    <div key={`${selection.kind}-${selection.id}`} className="animate-op-fade-up flex flex-col gap-6">
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
            <p className="mt-3 text-[16px] text-grey-body">{explanationFor(selection.kind, item)}</p>
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

      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1.4fr_1fr]">
        {tasks.length > 0 ? (
          <Card padding="p-0">
            <div className="px-6 pt-6 pb-2">
              <h3 className="text-[18px] font-bold">Tasks</h3>
            </div>
            {tasks.map((task) => (
              <div
                key={task.id}
                className="flex items-center justify-between gap-4 border-t border-grey-fill px-6 py-4 first:border-t-0"
              >
                <div className="min-w-0">
                  <div className="truncate text-[15px] font-bold">{task.name}</div>
                  <div className="mt-0.5 text-[13px] text-grey-secondary">
                    {task.id} · {task.source}
                    {task.confidence != null ? ` · ${Math.round(task.confidence * 100)}%` : ""}
                  </div>
                </div>
                <RagChip tone={toneForRag(task.rag)}>{task.rag}</RagChip>
              </div>
            ))}
          </Card>
        ) : null}

        <Card variant="grey" padding="p-6" className={cn(tasks.length === 0 && "lg:col-span-2")}>
          <h3 className="text-[18px] font-bold">Details</h3>
          <dl className="mt-3 flex flex-col gap-2.5">
            {detailRows(selection.kind, item, {
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

function explanationFor(kind: DeliveryKind, item: DirectoryItemResponse): string {
  if (kind === "program") {
    return `${item.project_ids.length} projects · ${item.workstream_ids.length} workstreams · ${item.pod_ids.length} pods roll up into this program.`;
  }
  if (item.rag === "red") return "Status is blocked based on the latest confirmed or inferred signal.";
  if (item.rag === "amber") return "Status is at risk based on the latest confirmed or inferred signal.";
  if (item.rag === "green") return "Status is on track based on the latest confirmed or inferred signal.";
  return "No confirmed or inferred status is available yet.";
}

function detailRows(
  kind: DeliveryKind,
  item: DirectoryItemResponse,
  data: {
    progress?: { total_tasks: number; green_tasks: number; amber_tasks: number; red_tasks: number; unknown_tasks: number; confidence: number | null; source: string } | undefined;
    checkins: { confirmed: number; developers: unknown[] } | undefined;
    blockers: { blockers: unknown[] } | undefined;
  },
): [string, string][] {
  if (kind === "program") {
    return [
      ["Projects", String(item.project_ids.length)],
      ["Workstreams", String(item.workstream_ids.length)],
      ["Pods", String(item.pod_ids.length)],
    ];
  }
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
      ["Owner", stringMeta(metadata.owner_id)],
      ["TPM", stringMeta(metadata.tpm_id)],
      ["SM", stringMeta(metadata.sm_id)],
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
