import type { ReactNode } from "react";

import type { DirectoryItemResponse } from "../../api/schema";
import { toneForRag, toneHex } from "../../lib/status";
import type { DeliveryKind } from "../../lib/useDeliverySelection";
import { cn } from "../../lib/utils";

function Dot({ item }: { item: DirectoryItemResponse }) {
  const tone = toneForRag(item.rag);
  return (
    <span
      className={cn(
        "inline-block h-2 w-2 shrink-0 rounded-full",
        item.rag === "red" && "animate-op-pulse",
      )}
      style={{ backgroundColor: toneHex[tone] }}
    />
  );
}

function NodeRow({
  item,
  kind,
  active,
  onSelect,
  trailing,
}: {
  item: DirectoryItemResponse;
  kind: DeliveryKind;
  active: boolean;
  onSelect: (kind: DeliveryKind, id: string) => void;
  trailing?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(kind, item.id)}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-2xl px-3.5 py-2.5 text-left text-[14px]",
        active ? "bg-ink font-bold text-white" : "text-ink hover:bg-grey-hover",
      )}
    >
      <Dot item={item} />
      <span className="min-w-0 flex-1 truncate">{item.name}</span>
      {trailing ? (
        <span className={cn("shrink-0 text-xs", active ? "text-white/70" : "text-grey-secondary")}>
          {trailing}
        </span>
      ) : null}
      <span
        className={cn(
          "shrink-0 text-[10px] font-bold uppercase tracking-wide",
          active ? "text-white/60" : "text-grey-disabled",
        )}
      >
        {kind}
      </span>
    </button>
  );
}

/** Indented children with a tree guide line on the left. */
function Branch({ children }: { children: ReactNode }) {
  return (
    <div className="ml-[18px] flex flex-col gap-1 border-l-2 border-grey-border pl-2.5">
      {children}
    </div>
  );
}

function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <div className="mt-4 px-3.5 pb-1 text-[11px] font-bold uppercase tracking-wide text-grey-secondary">
      {children}
    </div>
  );
}

export function DeliveryNavigator({
  programs,
  projects,
  workstreams,
  pods,
  selection,
  onSelect,
}: {
  programs: DirectoryItemResponse[];
  projects: DirectoryItemResponse[];
  workstreams: DirectoryItemResponse[];
  pods: DirectoryItemResponse[];
  selection: { kind: DeliveryKind; id: string };
  onSelect: (kind: DeliveryKind, id: string) => void;
}) {
  const linkedProjectIds = new Set(
    programs.flatMap((program) =>
      projects.filter((p) => p.program_ids.includes(program.id)).map((p) => p.id),
    ),
  );
  const unlinkedProjects = projects.filter((p) => !linkedProjectIds.has(p.id));

  const renderProject = (project: DirectoryItemResponse) => {
    const children = workstreams.filter((ws) => ws.project_ids.includes(project.id));
    return (
      <div key={project.id} className="flex flex-col gap-1">
        <NodeRow
          item={project}
          kind="project"
          active={selection.kind === "project" && selection.id === project.id}
          onSelect={onSelect}
        />
        {children.length > 0 ? (
          <Branch>
            {children.map((ws) => (
              <NodeRow
                key={ws.id}
                item={ws}
                kind="workstream"
                active={selection.kind === "workstream" && selection.id === ws.id}
                onSelect={onSelect}
                trailing={typeof ws.metadata.phase === "string" ? ws.metadata.phase : undefined}
              />
            ))}
          </Branch>
        ) : null}
      </div>
    );
  };

  return (
    <div className="flex flex-col gap-1 rounded-3xl bg-grey-fill p-4">
      <SectionLabel>Hierarchy · program → project → workstream</SectionLabel>
      {programs.map((program) => {
        const children = projects.filter((p) => p.program_ids.includes(program.id));
        return (
          <div key={program.id} className="flex flex-col gap-1">
            <NodeRow
              item={program}
              kind="program"
              active={selection.kind === "program" && selection.id === program.id}
              onSelect={onSelect}
            />
            {children.length > 0 ? <Branch>{children.map(renderProject)}</Branch> : null}
          </div>
        );
      })}

      {unlinkedProjects.length > 0 ? (
        <>
          <SectionLabel>Projects not linked to a program</SectionLabel>
          {unlinkedProjects.map(renderProject)}
        </>
      ) : null}

      <SectionLabel>Pods · teams working across the hierarchy</SectionLabel>
      <div className="flex flex-wrap gap-2 px-1">
        {pods.map((pod) => {
          const active = selection.kind === "pod" && selection.id === pod.id;
          const tone = toneForRag(pod.rag);
          return (
            <button
              key={pod.id}
              type="button"
              onClick={() => onSelect("pod", pod.id)}
              className={cn(
                "flex items-center gap-2 rounded-full px-3.5 py-2 text-[13px] font-bold",
                active ? "bg-ink text-white" : "bg-white text-ink hover:bg-grey-hover",
              )}
            >
              <span
                className="inline-block h-2 w-2 rounded-full"
                style={{ backgroundColor: toneHex[tone] }}
              />
              {pod.name}
            </button>
          );
        })}
      </div>

      <div className="mt-4 flex flex-wrap gap-x-4 gap-y-1 border-t border-grey-border px-3.5 pt-3 text-[11px] text-grey-secondary">
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: toneHex.success }} />
          on track
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: toneHex.warning }} />
          at risk
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: toneHex.danger }} />
          blocked
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2 w-2 rounded-full" style={{ backgroundColor: toneHex.neutral }} />
          unknown
        </span>
      </div>
    </div>
  );
}
