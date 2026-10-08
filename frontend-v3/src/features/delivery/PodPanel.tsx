import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { useDayWords } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { Panel, Row } from "../../components/ui/Bits";
import { boardMeta, boardRag, boardWord, repliedCount } from "../../lib/checkinWords";
import { formatDay } from "../../lib/format";
import { readState } from "../../lib/readState";
import { FactorsPanel, NodeHeader, Related } from "./NodeBits";
import { PodDateStrips } from "./DeliveryStrips";
import { PodTasksPanel } from "./PodTasks";
import { reasonLine, type Finder } from "./factors";

/**
 * A pod: members and who replied today, open blockers, and its tasks (blocked
 * first, each with owners and the blockers on it). Check-ins and blockers are
 * per person, so only the roles that read a pod's people get them.
 */
export function PodPanel({ pod, find }: { pod: DirectoryItemResponse; find: Finder }) {
  const { canReadPodDetail } = useRole();
  const day = useDayWords();
  const on = canReadPodDetail;
  const rollup = useQuery({
    queryKey: ["pod", pod.id, "rollup"],
    queryFn: () => apiClient.podRollup(pod.id),
    enabled: on,
  });
  const checkins = useQuery({
    queryKey: ["pod", pod.id, "checkins"],
    queryFn: () => apiClient.podCheckins(pod.id),
    enabled: on,
  });
  const blockers = useQuery({
    queryKey: ["pod", pod.id, "blockers"],
    queryFn: () => apiClient.podBlockers(pod.id),
    enabled: on,
  });

  const c = checkins.data;
  const total = c ? c.confirmed + c.partial + c.stale + c.missing : 0;

  return (
    <>
      <NodeHeader
        kind="Pod"
        name={pod.name}
        rag={rollup.data?.rag ?? pod.rag}
        reason={
          rollup.data
            ? reasonLine(rollup.data.factors, rollup.data.source_names, rollup.data.rag)
            : undefined
        }
        read={on ? readState(rollup) : undefined}
      />
      <div className="mb-5 grid gap-2">
        <Related
          label="Projects"
          kind="project"
          items={pod.project_ids.map((id) => find("project", id))}
        />
        <Related
          label="Workstreams"
          kind="workstream"
          items={pod.workstream_ids.map((id) => find("workstream", id))}
        />
        <p className="text-[13px] text-grey-body">
          {pod.member_ids.length} {pod.member_ids.length === 1 ? "member" : "members"}
          {c
            ? ` · ${repliedCount({ confirmed: c.confirmed, partial: c.partial, total })} ${day}`
            : ""}
          {blockers.data ? ` · ${blockers.data.blockers.length} open blockers` : ""}
        </p>
      </div>
      <div className="mb-5">
        <PodDateStrips pod={pod} />
      </div>
      {/* Per person, so only for the roles that read a pod's people. */}
      {on ? (
        <PanelState isLoading={rollup.isLoading} error={rollup.error}>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
            <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
              <Panel
                title={`Check-ins ${day}`}
                note={
                  c
                    ? repliedCount({ confirmed: c.confirmed, partial: c.partial, total })
                    : undefined
                }
              >
                <PanelState
                  isLoading={checkins.isLoading}
                  error={checkins.error}
                  isEmpty={(c?.developers ?? []).length === 0}
                  emptyText="Nobody in this pod is asked to check in."
                >
                  <ul>
                    {(c?.developers ?? []).map((dev) => (
                      <Row
                        key={dev.developer_id}
                        rag={boardRag(dev)}
                        title={dev.developer_name}
                        meta={boardMeta(dev, c?.as_of ?? "", { day: formatDay, today: day })}
                        right={boardWord(dev, c?.as_of ?? "").word}
                      />
                    ))}
                  </ul>
                </PanelState>
              </Panel>
              <Panel title="Open blockers" note="oldest first">
                <PanelState
                  isLoading={blockers.isLoading}
                  error={blockers.error}
                  isEmpty={(blockers.data?.blockers ?? []).length === 0}
                  emptyText="No open blockers."
                >
                  <ul>
                    {[...(blockers.data?.blockers ?? [])]
                      .sort((a, b) => b.age_days - a.age_days)
                      .map((b) => (
                        <Row
                          key={b.id}
                          rag={b.age_days >= 7 ? "red" : "amber"}
                          accent={b.age_days >= 7 ? "red" : "amber"}
                          title={b.description}
                          meta={`${b.owner_name}${b.work_item_ref ? ` · ${b.work_item_ref.id}` : ""} · since ${formatDay(b.first_seen_on)}`}
                          right={`${b.age_days}d`}
                        />
                      ))}
                  </ul>
                </PanelState>
              </Panel>
            </div>
            {rollup.data ? (
              <FactorsPanel factors={rollup.data.factors} names={rollup.data.source_names} />
            ) : null}
            <PodTasksPanel podId={pod.id} />
          </div>
        </PanelState>
      ) : null}
    </>
  );
}
