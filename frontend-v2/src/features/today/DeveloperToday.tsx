import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { TextArea, TextInput } from "../../components/ui/Field";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { todayIso } from "../../lib/today";
import { toneForRag } from "../../lib/status";

interface BlockerRow {
  blocker_id: string | null;
  description: string;
  work_item_id: string | null;
  resolved: boolean;
}

export function DeveloperToday() {
  const asOf = todayIso();
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [correctOpen, setCorrectOpen] = useState(false);
  const [summary, setSummary] = useState("");
  const [blockerRows, setBlockerRows] = useState<BlockerRow[]>([]);
  const [etaChange, setEtaChange] = useState("");

  const focus = useQuery({
    queryKey: ["persona", "focus", asOf],
    queryFn: () => apiClient.focus(asOf),
  });
  const myStatus = useQuery({
    queryKey: ["persona", "my-status", asOf],
    queryFn: () => apiClient.myStatus(asOf),
  });
  const myRequests = useQuery({
    queryKey: ["persona", "my-cross-person-requests"],
    queryFn: () => apiClient.myCrossPersonRequests(),
  });

  useEffect(() => {
    if (myStatus.data) {
      setSummary(myStatus.data.summary);
      const details = myStatus.data.blocker_details ?? [];
      setBlockerRows(
        details.length
          ? details.map((detail) => ({
              blocker_id: detail.blocker_id,
              description: detail.description,
              work_item_id: detail.work_item_id,
              resolved: false,
            }))
          : myStatus.data.blockers.map((description) => ({
              blocker_id: null,
              description,
              work_item_id: null,
              resolved: false,
            })),
      );
      setEtaChange(myStatus.data.eta_change_days != null ? String(myStatus.data.eta_change_days) : "");
    }
  }, [myStatus.data]);

  const confirmStatus = useMutation({
    mutationFn: () => apiClient.confirmMyStatus(asOf),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["persona", "my-status"] });
      await queryClient.invalidateQueries({ queryKey: ["persona", "focus"] });
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Confirm failed"),
  });

  const correctStatus = useMutation({
    mutationFn: () => {
      const rows = blockerRows.filter((row) => row.description.trim());
      return apiClient.correctMyStatus(
        {
          summary,
          blockers: rows.filter((row) => !row.resolved).map((row) => row.description.trim()),
          blocker_items: rows.map((row) => ({
            blocker_id: row.blocker_id,
            description: row.description.trim(),
            work_item_id: row.work_item_id,
            pod_id: null,
            resolved: row.resolved,
          })),
          eta_change_days: etaChange.trim() ? Number(etaChange) : null,
        },
        asOf,
      );
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["persona", "my-status"] });
      await queryClient.invalidateQueries({ queryKey: ["persona", "focus"] });
      setCorrectOpen(false);
    },
    onError: (error) => toast.error(error instanceof Error ? error.message : "Save failed"),
  });

  const confirmed = myStatus.data?.developer_confirmed ?? false;
  const blockerDetails = myStatus.data?.blocker_details ?? [];
  const blockerList = myStatus.data?.blockers ?? focus.data?.blockers ?? [];
  const etaDays = myStatus.data?.eta_change_days ?? null;

  return (
    <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[1.5fr_1fr]">
      <div className="flex flex-col gap-6">
        <Card variant="grey" padding="p-7" animateDelay={70}>
          <div className="flex items-center justify-between gap-4">
            <h2 className="text-[22px] font-bold">Your check-in</h2>
            <span className="text-[13px] text-grey-secondary">drafted from Slack · 09:12</span>
          </div>
          <p className="mt-3.5 text-[17px] leading-snug">
            {focus.data?.summary ?? myStatus.data?.summary ?? "No check-in on record yet."}
          </p>
          {(blockerDetails.length > 0 || blockerList.length > 0 || etaDays != null) && (
            <div className="mt-4 flex flex-wrap gap-2.5">
              {blockerDetails.length > 0
                ? blockerDetails.map((detail) => (
                    <RagChip
                      key={detail.blocker_id}
                      tone={detail.unattributed ? "warning" : "danger"}
                      dot
                      pulse={!detail.unattributed}
                    >
                      {detail.description}
                      {detail.work_item_id ? (
                        <span className="font-medium opacity-70">· {detail.work_item_id}</span>
                      ) : null}
                      {detail.unattributed ? (
                        <span className="font-medium opacity-70">· unattributed</span>
                      ) : null}
                    </RagChip>
                  ))
                : blockerList.map((blocker, index) => (
                    <RagChip key={index} tone="danger" dot pulse>
                      {blocker}
                    </RagChip>
                  ))}
              {etaDays != null ? (
                <RagChip tone="warning">
                  ETA {etaDays >= 0 ? "+" : ""}
                  {etaDays} days
                </RagChip>
              ) : null}
            </div>
          )}
          <div className="mt-5 flex items-center gap-3.5">
            {confirmed ? (
              <Pill variant="success" size="lg">
                <CheckCircle2 size={18} />
                Check-in confirmed
              </Pill>
            ) : (
              <Pill
                variant="primary"
                size="lg"
                onClick={() => confirmStatus.mutate()}
                disabled={confirmStatus.isPending}
              >
                <CheckCircle2 size={18} />
                {confirmStatus.isPending ? "Confirming…" : "Confirm check-in"}
              </Pill>
            )}
            <Pill variant="ghost" size="lg" onClick={() => setCorrectOpen(true)}>
              Correct details
            </Pill>
          </div>
        </Card>

        <Card padding="p-0" animateDelay={140}>
          <div className="flex items-center justify-between px-5 pt-5 pb-2">
            <h2 className="text-[18px] font-bold">Focus today</h2>
            <span className="text-[13px] text-grey-secondary">ranked by urgency</span>
          </div>
          {(focus.data?.focus ?? []).map((item, index) => (
            <div
              key={index}
              className="flex items-center gap-3.5 border-t border-grey-fill px-5 py-4 first:border-t-0"
            >
              <span
                className={
                  item.kind === "blocker"
                    ? "h-2.5 w-2.5 shrink-0 animate-op-pulse rounded-full bg-rag-red"
                    : "h-2.5 w-2.5 shrink-0 rounded-full bg-rag-info"
                }
              />
              <div className="min-w-0 flex-1">
                <div className="text-[16px] font-bold">{item.label}</div>
                <div className="mt-0.5 text-[13px] text-grey-secondary">
                  {item.deadline ?? item.source_ref.kind}
                </div>
              </div>
              <span className="text-xs font-bold uppercase tracking-wide text-grey-secondary">
                {item.kind}
              </span>
            </div>
          ))}
          {focus.data && focus.data.focus.length === 0 ? (
            <div className="px-5 py-6 text-sm text-grey-secondary">Nothing needs attention.</div>
          ) : null}
        </Card>

        <Card padding="p-0" animateDelay={210}>
          <div className="flex items-center justify-between px-5 pt-5 pb-2">
            <h2 className="text-[18px] font-bold">Your tasks</h2>
            <span className="text-[13px] text-grey-secondary">
              source-linked · silence is never green
            </span>
          </div>
          {(focus.data?.tasks ?? []).map((task) => (
            <div
              key={task.id}
              className="flex items-center gap-3.5 border-t border-grey-fill px-5 py-4 first:border-t-0"
            >
              <div className="min-w-0 flex-1">
                <div className="truncate text-[16px] font-bold">{task.name}</div>
                <div className="mt-0.5 text-[13px] text-grey-secondary">
                  {task.id} · {task.source}
                </div>
              </div>
              <RagChip tone={toneForRag(task.rag)}>{task.rag}</RagChip>
            </div>
          ))}
        </Card>
      </div>

      <div className="flex flex-col gap-6">
        <Card variant="grey" padding="p-0" animateDelay={280}>
          <div className="flex items-center justify-between px-5 pt-5 pb-2">
            <h2 className="text-[18px] font-bold">Waiting on you</h2>
            <button
              type="button"
              onClick={() => navigate("/coordination")}
              className="text-[14px] font-bold text-magenta"
            >
              All requests
            </button>
          </div>
          {(myRequests.data?.requests ?? []).map((request) => (
            <div key={request.source_correlation_id} className="px-5 py-3.5">
              <div className="text-[15px] font-bold">{request.note}</div>
              <div className="mt-0.5 text-[13px] text-grey-secondary">
                from {request.counterpart_display_name ?? request.raw_name ?? request.requester_id}
              </div>
            </div>
          ))}
          {myRequests.data && myRequests.data.requests.length === 0 ? (
            <div className="px-5 py-6 text-sm text-grey-secondary">Nothing waiting on you.</div>
          ) : null}
        </Card>

        <Card padding="p-6" animateDelay={350}>
          <h2 className="text-[18px] font-bold">Why this matters</h2>
          <p className="mt-3 text-[14px] leading-relaxed text-grey-body">
            Your check-in feeds every rollup above you — pod, project, and program. Silence is
            never read as green: an unconfirmed status stays visible as stale until you confirm or
            correct it, so leaders always see what's real.
          </p>
        </Card>
      </div>

      <Modal open={correctOpen} onOpenChange={setCorrectOpen} title="Correct your check-in">
        <div className="flex flex-col gap-4">
          <div>
            <label className="text-[13px] font-bold text-grey-secondary">Summary</label>
            <TextArea
              className="mt-1.5"
              value={summary}
              onChange={(event) => setSummary(event.target.value)}
            />
          </div>
          <div>
            <label className="text-[13px] font-bold text-grey-secondary">Blockers</label>
            <div className="mt-1.5 flex flex-col gap-2.5">
              {blockerRows.map((row, index) => (
                <div
                  key={row.blocker_id ?? `new-${index}`}
                  className="flex flex-col gap-2 rounded-2xl border border-grey-border p-3"
                >
                  <TextInput
                    value={row.description}
                    placeholder="Blocker description"
                    className={row.resolved ? "line-through opacity-60" : undefined}
                    onChange={(event) =>
                      setBlockerRows((rows) =>
                        rows.map((item, i) =>
                          i === index ? { ...item, description: event.target.value } : item,
                        ),
                      )
                    }
                  />
                  <div className="flex items-center gap-2">
                    <select
                      value={row.work_item_id ?? ""}
                      disabled={row.resolved}
                      className="h-9 min-w-0 flex-1 rounded-full border border-grey-border bg-white px-3 text-[13px] outline-none focus:border-ink disabled:opacity-60"
                      onChange={(event) =>
                        setBlockerRows((rows) =>
                          rows.map((item, i) =>
                            i === index
                              ? { ...item, work_item_id: event.target.value || null }
                              : item,
                          ),
                        )
                      }
                    >
                      <option value="">No work item</option>
                      {(focus.data?.tasks ?? []).map((task) => (
                        <option key={task.id} value={task.id}>
                          {task.name}
                        </option>
                      ))}
                    </select>
                    <Pill
                      variant="ghost"
                      size="sm"
                      className="shrink-0"
                      onClick={() =>
                        setBlockerRows((rows) =>
                          rows.map((item, i) =>
                            i === index ? { ...item, resolved: !item.resolved } : item,
                          ),
                        )
                      }
                    >
                      {row.resolved ? "Reopen" : "Resolve"}
                    </Pill>
                    {row.resolved ? <RagChip tone="success">resolved</RagChip> : null}
                  </div>
                </div>
              ))}
              <Pill
                variant="ghost"
                size="sm"
                className="self-start"
                onClick={() =>
                  setBlockerRows((rows) => [
                    ...rows,
                    { blocker_id: null, description: "", work_item_id: null, resolved: false },
                  ])
                }
              >
                Add blocker
              </Pill>
            </div>
          </div>
          <div>
            <label className="text-[13px] font-bold text-grey-secondary">ETA change (days)</label>
            <TextInput
              className="mt-1.5"
              type="number"
              value={etaChange}
              onChange={(event) => setEtaChange(event.target.value)}
            />
          </div>
          <div className="flex justify-end gap-3">
            <Pill variant="ghost" size="md" onClick={() => setCorrectOpen(false)}>
              Cancel
            </Pill>
            <Pill
              variant="primary"
              size="md"
              onClick={() => correctStatus.mutate()}
              disabled={correctStatus.isPending}
            >
              {correctStatus.isPending ? "Saving…" : "Save correction"}
            </Pill>
          </div>
        </div>
      </Modal>
    </div>
  );
}
