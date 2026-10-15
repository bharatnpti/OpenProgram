import { useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse } from "../../api/schema";
import { usePods } from "../../app/directory";
import { podTabPath } from "../../app/links";
import { useRole } from "../../app/role";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { formatDay } from "../../lib/format";
import { PERSON_KEY_WORDS, plural } from "../../lib/words";
import { FactorsPanel, Facts } from "../delivery/NodeBits";
import { ProgressSummary, TaskTable } from "../delivery/ProgressBlock";
import { metadataFacts } from "../delivery/factors";
import { byTarget, workstreamLine } from "./workstreamWords";

/**
 * A project's workstreams, on its Will we make the date?, only where it uses
 * any (they are optional; pods carry the work): one row each with its type,
 * phase, target date, the people who run it and its tasks, nearest target
 * first, each opening in place on its facts, its pods, its progress and tasks,
 * and every reason behind its health today. `?workstream=` opens one. Its
 * colour is today's health, so the tile is on Early warnings' heat, not here
 * beside the date.
 */
export function Workstreams({ projectId }: { projectId: string }) {
  const { access } = useRole();
  const names = !access.hub.countsOnly;
  const [search] = useSearchParams();
  const asked = search.get("workstream");
  const list = useQuery({
    queryKey: ["project", projectId, "workstreams"],
    queryFn: () => apiClient.projectWorkstreams(projectId),
  });
  const inUse = byTarget((list.data ?? []).filter((item) => item.in_use !== false));
  if (!list.isLoading && !list.error && inUse.length === 0) return null;

  return (
    <section aria-labelledby="workstreams-h">
      <SectionHeader
        id="workstreams-h"
        title="Workstreams"
        meta={
          list.data
            ? `${plural(inUse.length, "workstream", "workstreams")} · open one for its tasks and reasons`
            : undefined
        }
      />
      <PanelState isLoading={list.isLoading} error={list.error} onRetry={() => void list.refetch()}>
        <ul className="grid grid-cols-[minmax(0,1fr)] gap-2">
          {inUse.map((item) => (
            <WorkstreamRow
              key={item.id}
              item={item}
              projectId={projectId}
              names={names}
              asked={asked === item.id}
            />
          ))}
        </ul>
      </PanelState>
    </section>
  );
}

function WorkstreamRow({
  item,
  projectId,
  names,
  asked,
}: {
  item: DirectoryItemResponse;
  projectId: string;
  names: boolean;
  asked: boolean;
}) {
  const ref = useRef<HTMLDetailsElement>(null);
  // Its contents are read once it is first opened; a link naming it opens it.
  const [opened, setOpened] = useState(asked);
  useEffect(() => {
    if (!asked) return;
    const timer = window.setTimeout(() => ref.current?.scrollIntoView({ block: "start" }), 300);
    return () => window.clearTimeout(timer);
  }, [asked]);
  const line = workstreamLine(item, names, formatDay);
  return (
    <li className="min-w-0">
      <details
        ref={ref}
        open={asked || undefined}
        onToggle={(event) => {
          if ((event.currentTarget as HTMLDetailsElement).open) setOpened(true);
        }}
        className="group scroll-mt-28 rounded-3xl border border-grey-border bg-(--op-viz-surface) p-4 max-sm:rounded-[20px]"
      >
        <summary className="flex min-h-11 cursor-pointer list-none items-start justify-between gap-3 [&::-webkit-details-marker]:hidden">
          <span className="min-w-0">
            <span className="block text-[15px] font-extrabold text-ink break-words">
              {item.name}
            </span>
            {line ? (
              <span className="mt-0.5 block text-[12px] text-grey-secondary">{line}</span>
            ) : null}
          </span>
          <span className="flex-none text-[12px] font-bold text-grey-secondary">
            <span className="group-open:hidden">Open</span>
            <span className="hidden group-open:inline">Close</span>
          </span>
        </summary>
        {opened ? <WorkstreamDetail item={item} projectId={projectId} names={names} /> : null}
      </details>
    </li>
  );
}

/** What the old workstream panel had: facts, its pods, its progress and tasks, and its reasons. */
function WorkstreamDetail({
  item,
  projectId,
  names,
}: {
  item: DirectoryItemResponse;
  projectId: string;
  names: boolean;
}) {
  const pods = usePods();
  const progress = useQuery({
    queryKey: ["workstream", item.id, "progress"],
    queryFn: () => apiClient.workstreamProgress(item.id),
  });
  const p = progress.data;
  const facts: [string, string][] = [
    ...metadataFacts(item.metadata),
    ...(names
      ? item.people.flatMap((person): [string, string][] =>
          person.name ? [[PERSON_KEY_WORDS[person.key] ?? person.key, person.name]] : [],
        )
      : []),
  ];
  const itsPods = (pods.data ?? []).filter(
    (pod) => item.pod_ids.includes(pod.id) && pod.project_ids.includes(projectId),
  );
  return (
    <div className="mt-3 grid grid-cols-[minmax(0,1fr)] gap-4">
      {facts.length > 0 ? <Facts facts={facts} /> : null}
      {itsPods.length > 0 ? (
        <p className="flex flex-wrap items-center gap-2 text-[13px]">
          <span className="font-bold text-grey-secondary">Pods</span>
          {itsPods.map((pod) => (
            <Link
              key={pod.id}
              to={podTabPath(projectId, pod.id, "date")}
              className="inline-flex min-h-9 items-center rounded-full border border-grey-border px-3 font-bold text-ink no-underline hover:bg-grey-fill max-sm:min-h-11"
            >
              {pod.name}
            </Link>
          ))}
        </p>
      ) : null}
      <PanelState
        isLoading={progress.isLoading}
        error={progress.error}
        onRetry={() => void progress.refetch()}
      >
        {p ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <ProgressSummary progress={p} />
            <TaskTable tasks={p.tasks} />
            {/* A reader who sees no names reads no one behind the reasons. */}
            {names ? (
              <FactorsPanel
                title="Today's health: every reason"
                factors={p.factors}
                names={p.source_names}
                always
              />
            ) : null}
          </div>
        ) : null}
      </PanelState>
    </div>
  );
}
