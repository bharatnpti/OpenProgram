import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { PodCheckinsResponse } from "../../api/schema";
import {
  podsOfPerson,
  programsOfProjects,
  useMemberId,
  usePods,
  usePrograms,
  useProjects,
} from "../../app/directory";
import { useRole } from "../../app/role";
import { useDayWords, useShownDay } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { ChipPicker, Greeting, Panel, Row } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { ragSeverity, toneForRag, type BadgeTone } from "../../lib/status";
import { greetingTitle, plural, sourceLine, spaced, todayEyebrow } from "../../lib/words";
import { podCheckinMeta } from "./checkin";
import { WaitingOnYou } from "./WaitingOnYou";

type CheckinState = PodCheckinsResponse["developers"][number]["state"];
const STATE_TONE: Record<CheckinState, BadgeTone> = {
  confirmed: "success",
  partial: "warning",
  stale: "warning",
  missing: "neutral",
};
const STATE_RAG = {
  confirmed: "green",
  partial: "amber",
  stale: "amber",
  missing: "unknown",
} as const;
const STATE_WORD: Record<CheckinState, string> = {
  confirmed: "confirmed",
  partial: "partial",
  stale: "stale",
  missing: "no status",
};

const NEEDS = "a scrum master, manager or admin";

/**
 * A scrum master's two morning questions: who has checked in across my pods,
 * and how old is each blocker. Defaults to the pods the person runs: a member
 * of, or named as the scrum master contact of (the backend's own rule).
 */
export function ScrumMasterToday() {
  const shownDay = useShownDay();
  // "today", or "on Mon 5 Oct" while a past day is shown.
  const day = useDayWords();
  const { canReadPodDetail, roleLabel, greetingName } = useRole();
  const memberId = useMemberId();
  const pods = usePods();
  const projects = useProjects();
  const programs = usePrograms();
  const { pods: mine, own } = podsOfPerson(pods.data ?? [], memberId);
  const [chosen, setChosen] = useState("");
  const podId = mine.some((p) => p.id === chosen) ? chosen : (mine[0]?.id ?? "");
  const pod = mine.find((p) => p.id === podId);
  const podProjects = (projects.data ?? []).filter((p) => pod?.project_ids.includes(p.id));

  const checkins = useQuery({
    queryKey: ["pod", podId, "checkins"],
    queryFn: () => apiClient.podCheckins(podId),
    enabled: Boolean(podId) && canReadPodDetail,
  });
  const blockers = useQuery({
    queryKey: ["pod", podId, "blockers"],
    queryFn: () => apiClient.podBlockers(podId),
    enabled: Boolean(podId) && canReadPodDetail,
  });
  const rollup = useQuery({
    queryKey: ["pod", podId, "rollup"],
    queryFn: () => apiClient.podRollup(podId),
    enabled: Boolean(podId) && canReadPodDetail,
  });

  const c = checkins.data;
  const total = c ? c.confirmed + c.partial + c.stale + c.missing : 0;
  const sortedBlockers = [...(blockers.data?.blockers ?? [])].sort(
    (a, b) => b.age_days - a.age_days,
  );
  const factors = [...(rollup.data?.factors ?? [])].sort(
    (a, b) => ragSeverity(b.contributes) - ragSeverity(a.contributes),
  );

  return (
    <>
      <Greeting
        eyebrow={todayEyebrow(
          programsOfProjects(programs.data ?? [], podProjects).map((p) => p.name),
          shownDay,
        )}
        title={greetingTitle(greetingName, roleLabel)}
        sub="Who has checked in across your pods, and how long each blocker has been open."
      />
      <PanelState
        needs="anyone with a member record"
        isLoading={pods.isLoading}
        error={pods.error}
        isEmpty={mine.length === 0}
        emptyText="No pods are configured yet."
      >
        <ChipPicker
          label="Pod"
          value={podId}
          onChange={setChosen}
          options={mine.map((p) => ({ value: p.id, label: p.name, rag: p.rag }))}
          note={
            own
              ? `${plural(mine.length, "pod", "pods")} · the pods you run or belong to`
              : `${plural(mine.length, "pod", "pods")} · you run no pod yet, so every pod is shown`
          }
        />
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
          <Panel
            title={`Check-ins ${day}`}
            note={c ? `${c.confirmed} of ${total} confirmed` : undefined}
          >
            <PanelState
              locked={!canReadPodDetail}
              needs={NEEDS}
              isLoading={checkins.isLoading}
              error={checkins.error}
              isEmpty={(c?.developers ?? []).length === 0}
              emptyText="Nobody in this pod is asked to check in."
            >
              <ul>
                {(c?.developers ?? []).map((dev) => (
                  <Row
                    key={dev.developer_id}
                    rag={STATE_RAG[dev.state]}
                    title={dev.developer_name}
                    meta={podCheckinMeta(dev, c?.as_of ?? "", { day: formatDay })}
                    right={
                      <RagChip tone={STATE_TONE[dev.state]} className="h-6 px-2.5 text-[12px]">
                        {STATE_WORD[dev.state]}
                      </RagChip>
                    }
                  />
                ))}
              </ul>
            </PanelState>
          </Panel>
          <Panel title="Open blockers" note={`${sortedBlockers.length} · oldest first`}>
            <PanelState
              locked={!canReadPodDetail}
              needs={NEEDS}
              isLoading={blockers.isLoading}
              error={blockers.error}
              isEmpty={sortedBlockers.length === 0}
              emptyText="No open blockers in this pod."
            >
              <ul>
                {sortedBlockers.map((b) => (
                  <Row
                    key={b.id}
                    rag={b.age_days >= 7 ? "red" : "amber"}
                    title={b.description}
                    meta={[
                      b.owner_name,
                      b.work_item_ref?.id,
                      // A blocker the owner stated is the norm; one inferred from
                      // delivery signals says so.
                      b.source === "confirmed" ? null : sourceLine(b.source),
                    ]
                      .filter(Boolean)
                      .join(" · ")}
                    right={`${b.age_days}d`}
                  />
                ))}
              </ul>
            </PanelState>
          </Panel>
          <Panel
            title={
              rollup.data
                ? `Why ${pod?.name ?? "this pod"} is ${rollup.data.rag}`
                : "Why this pod has its colour"
            }
            note={
              pod ? (
                <Link
                  to={`/delivery/pod/${pod.id}`}
                  className="font-bold max-sm:inline-flex max-sm:min-h-11 max-sm:items-center"
                >
                  Open in Delivery
                </Link>
              ) : undefined
            }
          >
            <PanelState
              locked={!canReadPodDetail}
              needs={NEEDS}
              isLoading={rollup.isLoading}
              error={rollup.error}
              isEmpty={factors.length === 0}
              emptyText={`Nothing recorded for this pod ${day}.`}
            >
              <ul>
                {factors.map((f, i) => (
                  <Row
                    key={`${f.kind}-${i}`}
                    rag={f.contributes}
                    title={f.description}
                    meta={rollup.data?.source_names[f.source_ref.id] ?? spaced(f.kind)}
                    right={
                      <RagChip tone={toneForRag(f.contributes)} className="h-6 px-2.5 text-[12px]">
                        {f.contributes}
                      </RagChip>
                    }
                  />
                ))}
              </ul>
            </PanelState>
          </Panel>
          <WaitingOnYou />
        </div>
      </PanelState>
    </>
  );
}
