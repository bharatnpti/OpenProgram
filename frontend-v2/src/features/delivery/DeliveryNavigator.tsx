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
  indent = false,
  trailing,
}: {
  item: DirectoryItemResponse;
  kind: DeliveryKind;
  active: boolean;
  onSelect: (kind: DeliveryKind, id: string) => void;
  indent?: boolean;
  trailing?: string;
}) {
  return (
    <button
      type="button"
      onClick={() => onSelect(kind, item.id)}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-2xl px-3.5 py-2.5 text-left text-[14px]",
        indent && "ml-[34px] w-[calc(100%-34px)]",
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
    </button>
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
  return (
    <div className="flex flex-col gap-1.5 rounded-3xl bg-grey-fill p-4">
      {programs.map((program) => (
        <NodeRow
          key={program.id}
          item={program}
          kind="program"
          active={selection.kind === "program" && selection.id === program.id}
          onSelect={onSelect}
        />
      ))}

      <div className="mt-3 px-3.5 text-xs font-bold uppercase tracking-wide text-grey-secondary">
        Projects &amp; workstreams
      </div>
      {projects.map((project) => (
        <div key={project.id} className="flex flex-col gap-1.5">
          <NodeRow
            item={project}
            kind="project"
            active={selection.kind === "project" && selection.id === project.id}
            onSelect={onSelect}
          />
          {workstreams
            .filter((ws) => ws.project_ids.includes(project.id))
            .map((ws) => (
              <NodeRow
                key={ws.id}
                item={ws}
                kind="workstream"
                active={selection.kind === "workstream" && selection.id === ws.id}
                onSelect={onSelect}
                indent
                trailing={typeof ws.metadata.phase === "string" ? ws.metadata.phase : undefined}
              />
            ))}
        </div>
      ))}

      <div className="mt-3 px-3.5 text-xs font-bold uppercase tracking-wide text-grey-secondary">
        Pods
      </div>
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
    </div>
  );
}
