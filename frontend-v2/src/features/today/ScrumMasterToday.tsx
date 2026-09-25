import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { apiClient } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { RagChip } from "../../components/ui/RagChip";
import { SegmentedBar } from "../../components/ui/SegmentedBar";
import { useRole } from "../../app/role";
import { resolveSelection } from "../../lib/selection";
import { WaitingOnYou } from "./WaitingOnYou";
import { todayIso } from "../../lib/today";

const STATE_TONE = {
  confirmed: "success",
  partial: "info",
  stale: "warning",
  missing: "danger",
} as const;

export function ScrumMasterToday() {
  const asOf = todayIso();
  const { actingAs } = useRole();
  const [chosenPodId, setChosenPodId] = useState("");

  const pods = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
  });

  // A scrum master runs their own pods, so default to one of those rather than
  // whichever pod happens to sort first. Falling back to every pod keeps this
  // working when nobody is being acted as (any real auth provider).
  const myPods = useMemo(() => {
    const all = pods.data ?? [];
    if (!actingAs) {
      return all;
    }
    const mine = all.filter((item) => item.member_ids.includes(actingAs.id));
    return mine.length > 0 ? mine : all;
  }, [actingAs, pods.data]);

  const podId = resolveSelection(chosenPodId, myPods);
  const pod = myPods.find((item) => item.id === podId);

  const checkins = useQuery({
    queryKey: ["persona", "checkins", podId, asOf],
    queryFn: () => apiClient.podCheckins(podId, asOf),
    enabled: Boolean(podId),
  });
  const blockers = useQuery({
    queryKey: ["persona", "blockers", podId, asOf],
    queryFn: () => apiClient.podBlockers(podId, asOf),
    enabled: Boolean(podId),
  });

  const total = checkins.data
    ? (checkins.data.confirmed ?? 0) +
      (checkins.data.partial ?? 0) +
      (checkins.data.stale ?? 0) +
      (checkins.data.missing ?? 0)
    : 0;

  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1.5fr_1fr]">
      <Card padding="p-0" animateDelay={70}>
        <div className="flex flex-wrap items-center justify-between gap-3 px-6 pt-6">
          <div className="flex min-w-0 items-center gap-3">
            <h2 className="truncate text-[20px] font-bold">{pod?.name ?? "Pod"} check-ins</h2>
            {myPods.length > 1 ? (
              <select
                aria-label="Pod"
                value={podId}
                onChange={(event) => setChosenPodId(event.target.value)}
                className="h-9 rounded-full border border-grey-border bg-white px-3 text-[13px] font-medium text-ink outline-none focus:border-magenta"
              >
                {myPods.map((item) => (
                  <option key={item.id} value={item.id}>
                    {item.name}
                  </option>
                ))}
              </select>
            ) : null}
          </div>
          {checkins.data ? (
            <RagChip tone="warning">
              {checkins.data.confirmed} of {total} confirmed
            </RagChip>
          ) : null}
        </div>
        {checkins.data ? (
          <div className="px-6 pt-4">
            <SegmentedBar
              height={10}
              segments={[
                {
                  value: checkins.data.confirmed ?? 0,
                  color: "var(--op-green)",
                  label: "Confirmed",
                },
                { value: checkins.data.partial ?? 0, color: "var(--op-info)", label: "Partial" },
                {
                  value: (checkins.data.stale ?? 0) + (checkins.data.missing ?? 0),
                  color: "var(--op-red)",
                  label: "Missing",
                },
              ]}
            />
          </div>
        ) : null}
        <div className="mt-4">
          {(checkins.data?.developers ?? []).map((developer) => (
            <div
              key={developer.developer_id}
              className="flex items-center justify-between gap-4 border-t border-grey-fill px-6 py-4 first:border-t-0"
            >
              <div>
                <div className="text-[15px] font-bold">{developer.developer_name}</div>
                {developer.summary ? (
                  <div className="mt-0.5 text-[13px] text-grey-secondary">{developer.summary}</div>
                ) : null}
              </div>
              <RagChip tone={STATE_TONE[developer.state]}>{developer.state}</RagChip>
            </div>
          ))}
        </div>
      </Card>

      <div className="flex flex-col gap-6">
        <Card variant="grey" padding="p-0" animateDelay={140}>
          <div className="px-6 pt-6 pb-2">
            <h2 className="text-[18px] font-bold">Blocker aging</h2>
          </div>
          {(blockers.data?.blockers ?? []).map((blocker) => {
            const fillColor =
              blocker.age_days >= 5
                ? "var(--op-red)"
                : blocker.age_days >= 2
                  ? "var(--op-amber)"
                  : "var(--op-info)";
            return (
              <div key={blocker.id} className="px-6 py-3.5">
                <div className="flex items-center justify-between gap-4">
                  <div className="text-[15px] font-bold">{blocker.description}</div>
                  <div className="shrink-0 text-[13px] font-bold text-grey-secondary">
                    {blocker.age_days}d
                  </div>
                </div>
                <div className="mt-1 text-[13px] text-grey-secondary">{blocker.owner_name}</div>
                {(blocker.work_item_ref ?? blocker.pod_ref) || blocker.unattributed ? (
                  <div className="mt-2 flex flex-wrap items-center gap-2">
                    {blocker.work_item_ref ? (
                      <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                        {blocker.work_item_ref.id}
                      </RagChip>
                    ) : blocker.pod_ref ? (
                      <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                        {blocker.pod_ref.id}
                      </RagChip>
                    ) : null}
                    {blocker.unattributed ? (
                      <RagChip tone="warning" className="h-6 px-2.5 text-[12px]">
                        unattributed
                      </RagChip>
                    ) : null}
                  </div>
                ) : null}
                <div className="mt-2 h-1.5 w-full overflow-hidden rounded-full bg-grey-border">
                  <div
                    className="animate-op-bar h-full rounded-full"
                    style={{
                      width: `${Math.min(100, blocker.age_days * 16)}%`,
                      backgroundColor: fillColor,
                    }}
                  />
                </div>
              </div>
            );
          })}
          {blockers.data && blockers.data.blockers.length === 0 ? (
            <div className="px-6 py-6 text-sm text-grey-secondary">No open blockers.</div>
          ) : null}
        </Card>

        <WaitingOnYou animateDelay={280} />
      </div>
    </div>
  );
}
