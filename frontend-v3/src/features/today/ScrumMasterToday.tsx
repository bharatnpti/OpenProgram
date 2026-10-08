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
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { boardMeta, boardRag, boardWord, repliedCount } from "../../lib/checkinWords";
import { greetingTitle, plural, sourceLine, todayEyebrow } from "../../lib/words";
import { FactorsPanel } from "../delivery/NodeBits";
import { PodDateStrips } from "../delivery/DeliveryStrips";
import { PodTasksPanel } from "../delivery/PodTasks";
import { reasonsBeyondBoard } from "../delivery/factors";
import { CheckinCard } from "./CheckinCard";
import { YourAsks } from "./YourAsks";

/**
 * A scrum master's morning, one pod at a time (`?pod=`): the pod's dates, its
 * open blockers (oldest first), who has checked in, whatever else sets its
 * colour, and the tasks its people hold. Opens on the pods the person runs: a
 * member of, or named as the scrum master contact of (the backend's own rule);
 * a pod named in the link is offered too.
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

  const c = checkins.data;
  const total = c ? c.confirmed + c.partial + c.stale + c.missing : 0;
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
        sub="How long each blocker has been open, and who has checked in across your pods."
      />
      <PanelState
        isLoading={pods.isLoading}
        error={pods.error}
        isEmpty={options.length === 0}
        emptyText="No pods are configured yet."
      >
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
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
          {pod ? <PodDateStrips pod={pod} /> : null}
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
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
            <Panel
              title={`Check-ins ${day}`}
              note={
                c ? repliedCount({ confirmed: c.confirmed, partial: c.partial, total }) : undefined
              }
            >
              <PanelState
                isLoading={checkins.isLoading}
                error={checkins.error}
                isEmpty={(c?.developers ?? []).length === 0}
                emptyText="Nobody in this pod is asked to check in."
              >
                <ul>
                  {(c?.developers ?? []).map((dev) => {
                    const word = boardWord(dev, c?.as_of ?? "");
                    return (
                      <Row
                        key={dev.developer_id}
                        rag={boardRag(dev)}
                        title={dev.developer_name}
                        meta={boardMeta(dev, c?.as_of ?? "", { day: formatDay, today: day })}
                        right={
                          <RagChip tone={word.tone} className="h-6 px-2.5 text-[12px]">
                            {word.word}
                          </RagChip>
                        }
                      />
                    );
                  })}
                </ul>
              </PanelState>
            </Panel>
          </div>
          {/* Whatever else sets the pod's colour: the blockers and check-ins are listed above. */}
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
                      <p className="mt-2 text-[12px] text-grey-secondary">And {why.tally} above.</p>
                    ) : null
                  }
                />
              ) : (
                <Panel title={`Why ${pod?.name ?? "this pod"} is ${rollup.data.rag}`}>
                  <p className="text-[14px] text-grey-body">
                    {why.tally
                      ? `Only what is listed above: ${why.tally}.`
                      : `Nothing recorded for this pod ${day}.`}
                  </p>
                </Panel>
              )
            ) : null}
          </PanelState>
          {podId ? <PodTasksPanel podId={podId} /> : null}
          {/* Their own check-in, asked in chat like everyone else's, and their asks. */}
          <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-4 lg:grid-cols-2">
            <CheckinCard compact />
            <YourAsks />
          </div>
        </div>
      </PanelState>
    </>
  );
}
