import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { Panel, RagBadge, Row } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { ragSeverity } from "../../lib/status";
import { sourceLine } from "../../lib/words";
import { FactorsPanel, NodeHeader, Related } from "./NodeBits";
import { PodDeliveryCard } from "./PodDeliveryCard";
import { reasonLine, type Finder } from "./factors";

const NEEDS = "a scrum master, manager or admin";

/**
 * A pod: members and who confirmed today, open blockers, and its tasks (blocked
 * first, each with owners and the blockers on it). Check-ins and blockers are
 * per person, so they open for a scrum master, manager or admin only.
 */
export function PodPanel({ pod, find }: { pod: DirectoryItemResponse; find: Finder }) {
  const { canReadPodDetail } = useRole();
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
  const tasks = useQuery({
    queryKey: ["pod", pod.id, "tasks"],
    queryFn: () => apiClient.podTasks(pod.id),
    enabled: on,
  });

  const c = checkins.data;
  const total = c ? c.confirmed + c.partial + c.stale + c.missing : 0;
  const sortedTasks = [...(tasks.data?.tasks ?? [])].sort(
    (a, b) => Number(b.blocked) - Number(a.blocked) || ragSeverity(b.rag) - ragSeverity(a.rag),
  );

  return (
    <>
      <NodeHeader
        kind="Pod"
        name={pod.name}
        rag={rollup.data?.rag ?? pod.rag}
        reason={
          on
            ? rollup.data
              ? reasonLine(rollup.data.factors, rollup.data.source_names)
              : undefined
            : "Check-ins, blockers and the reasons behind this pod's colour open for a scrum master, manager or admin."
        }
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
          {c ? ` · ${c.confirmed} of ${total} confirmed today` : ""}
          {blockers.data ? ` · ${blockers.data.blockers.length} open blockers` : ""}
        </p>
      </div>
      <PodDeliveryCard pod={pod} />
      <PanelState locked={!on} needs={NEEDS} isLoading={rollup.isLoading} error={rollup.error}>
        <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
            <Panel
              title="Check-ins today"
              note={c ? `${c.confirmed} of ${total} confirmed` : undefined}
            >
              <PanelState
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
                      rag={
                        dev.state === "confirmed"
                          ? "green"
                          : dev.state === "missing"
                            ? "unknown"
                            : "amber"
                      }
                      title={dev.developer_name}
                      meta={dev.summary || sourceLine(dev.source)}
                      right={dev.state}
                    />
                  ))}
                </ul>
              </PanelState>
            </Panel>
            <Panel title="Open blockers" note="oldest first">
              <PanelState
                needs={NEEDS}
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
          <PanelState
            needs={NEEDS}
            isLoading={tasks.isLoading}
            error={tasks.error}
            isEmpty={sortedTasks.length === 0}
            emptyText="No tasks are assigned to this pod's members within its remit."
          >
            <TableBox>
              <table className="w-full min-w-[680px] border-collapse">
                <thead>
                  <tr>
                    <th className={th}>Task</th>
                    <th className={th}>Status</th>
                    <th className={th}>Owners</th>
                    <th className={th}>Blockers</th>
                    <th className={th}>Due</th>
                  </tr>
                </thead>
                <tbody>
                  {sortedTasks.map((task) => (
                    <tr key={task.id}>
                      <td className={td}>
                        <span className="font-bold">{task.name}</span>
                        <span className="block text-[12px] text-grey-secondary">
                          {task.id}
                          {task.tracker_status ? ` · ${task.tracker_status}` : ""}
                        </span>
                      </td>
                      <td className={td}>
                        {task.blocked ? (
                          <RagChip tone="danger" className="h-6 px-2.5 text-[12px]">
                            blocked
                          </RagChip>
                        ) : (
                          <RagBadge rag={task.rag} />
                        )}
                      </td>
                      <td className={td}>{task.owners.map((o) => o.name).join(", ") || "—"}</td>
                      <td className={td}>
                        {task.open_blockers.length === 0
                          ? "—"
                          : task.open_blockers
                              .map((b) => `${b.description} (${b.age_days}d)`)
                              .join("; ")}
                      </td>
                      <td className={`${td} whitespace-nowrap`}>{formatDay(task.deadline)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableBox>
          </PanelState>
        </div>
      </PanelState>
    </>
  );
}
