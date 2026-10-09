import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../../api/client";
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
import { greetingTitle, plural, sourceLine, todayEyebrow } from "../../lib/words";
import { useAssistantSubject } from "../assistant/assistantContext";
import { FactorsPanel } from "../delivery/NodeBits";
import { PodDateStrips } from "../delivery/DeliveryStrips";
import { PodTasksPanel } from "../delivery/PodTasks";
import { reasonsBeyondBoard } from "../delivery/factors";
import { CheckinCard } from "./CheckinCard";
import { CheckinsPanel } from "./CheckinsPanel";
import { YourAsks } from "./YourAsks";

/**
 * A scrum master's morning, one pod at a time (`?pod=`). The main column leads
 * with what decides the pod's day: its dates, why it has its colour, the tasks
 * its people hold, then its open blockers (oldest first). Who has checked in
 * sits in a folding panel on the right, one line until opened, flagged red
 * while anyone is not green. Opens on the pods the person runs: a member of,
 * or named as the scrum master contact of (the backend's own rule); a pod
 * named in the link is offered too.
 */
export function ScrumMasterToday() {
  const shownDay = useShownDay();
  // "today", or "on Mon 5 Oct" while a past day is shown.
  const day = useDayWords();
  const { roleLabel, greetingName } = useRole();
  const memberId = useMemberId();
  const pods = usePods();
  const projects = useProjects();
  const programs = usePrograms();
  const [search, setSearch] = useSearchParams();
  const asked = search.get("pod");
  const { pods: mine, own } = podsOfPerson(pods.data ?? [], memberId);
  // A pod a link names (a palette jump, a Delivery link) is offered even when it is not theirs.
  const linked =
    asked && !mine.some((p) => p.id === asked)
      ? (pods.data ?? []).find((p) => p.id === asked)
      : undefined;
  const options = linked ? [linked, ...mine] : mine;
  const podId = options.some((p) => p.id === asked) ? (asked ?? "") : (options[0]?.id ?? "");
  const pod = options.find((p) => p.id === podId);
  const podProjects = (projects.data ?? []).filter((p) => pod?.project_ids.includes(p.id));
  const choose = (next: string) =>
    setSearch(
      (current) => {
        // Other parameters (the day being viewed) are the shell's; leave them.
        const params = new URLSearchParams(current);
        params.set("pod", next);
        return params;
      },
      { replace: true },
    );

  const checkins = useQuery({
    queryKey: ["pod", podId, "checkins"],
    queryFn: () => apiClient.podCheckins(podId),
    enabled: Boolean(podId),
  });
  const blockers = useQuery({
    queryKey: ["pod", podId, "blockers"],
    queryFn: () => apiClient.podBlockers(podId),
    enabled: Boolean(podId),
  });
  const rollup = useQuery({
    queryKey: ["pod", podId, "rollup"],
    queryFn: () => apiClient.podRollup(podId),
    enabled: Boolean(podId),
  });

  useAssistantSubject(pod ? { kind: "pod", name: pod.name } : null);
  const sortedBlockers = [...(blockers.data?.blockers ?? [])].sort(
    (a, b) => b.age_days - a.age_days,
  );
  const why = reasonsBeyondBoard(rollup.data?.factors ?? []);

  return (
    <>
      <Greeting
        eyebrow={todayEyebrow(
          programsOfProjects(programs.data ?? [], podProjects).map((p) => p.name),
          shownDay,
        )}
        title={greetingTitle(greetingName, roleLabel)}
        sub="Your pod's dates, why it has its colour, its work and how long each blocker has been open."
      />
      <PanelState
        isLoading={pods.isLoading}
        error={pods.error}
        isEmpty={options.length === 0}
        emptyText="No pods are configured yet."
      >
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
          <div className="grid min-w-0 grid-cols-[minmax(0,1fr)] content-start gap-4">
            <ChipPicker
              label="Pod"
              value={podId}
              onChange={choose}
              options={options.map((p) => ({ value: p.id, label: p.name, rag: p.rag }))}
              note={
                own
                  ? `${plural(mine.length, "pod", "pods")} · the pods you run or belong to`
                  : `${plural(mine.length, "pod", "pods")} · you run no pod yet, so every pod is shown`
              }
            />
            {pod ? <PodDateStrips pod={pod} /> : null}
            {/* Whatever else sets the pod's colour: the blockers and check-ins have panels of their own. */}
            <PanelState isLoading={rollup.isLoading} error={rollup.error}>
              {rollup.data ? (
                why.rest.length > 0 ? (
                  <FactorsPanel
                    always
                    title={`Why ${pod?.name ?? "this pod"} is ${rollup.data.rag}`}
                    factors={why.rest}
                    names={rollup.data.source_names}
                    footer={
                      why.tally ? (
                        <p className="mt-2 text-[12px] text-grey-secondary">
                          And {why.tally}, listed under {why.where}.
                        </p>
                      ) : null
                    }
                  />
                ) : (
                  <Panel title={`Why ${pod?.name ?? "this pod"} is ${rollup.data.rag}`}>
                    <p className="text-[14px] text-grey-body">
                      {why.tally
                        ? `Only ${why.tally}, listed under ${why.where}.`
                        : `Nothing recorded for this pod ${day}.`}
                    </p>
                  </Panel>
                )
              ) : null}
            </PanelState>
            {podId ? <PodTasksPanel podId={podId} /> : null}
            <Panel
              title="Open blockers"
              // A count is only true once the blockers are read: "0" is not "not read yet".
              note={blockers.data ? `${sortedBlockers.length} · oldest first` : "oldest first"}
            >
              <PanelState
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
                      accent={b.age_days >= 7 ? "red" : "amber"}
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
            {/* Their own check-in, asked in chat like everyone else's, and their asks. */}
            <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 xl:grid-cols-2">
              <CheckinCard compact />
              <YourAsks />
            </div>
          </div>
          {podId ? <CheckinsPanel read={checkins} day={day} who={memberId} /> : null}
        </div>
      </PanelState>
    </>
  );
}
