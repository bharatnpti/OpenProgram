import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Pencil, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  DeliveryDateRequest,
  ReleaseMatchKind,
  ScopeDeliveryResponse,
} from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { TextInput } from "../../components/ui/Field";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { useRole } from "../../app/role";
import { useViewingDate } from "../../app/viewingDate";
import { AdminSelect } from "../admin/AdminSelect";
import { FormField } from "../admin/FormField";
import { errorMessage } from "../admin/adminTypes";
import {
  VERDICT_LABELS,
  VERDICT_TONES,
  dayLabel,
  deliveryKey,
  historyLabel,
  movedLabel,
  targetSource,
  teamLabel,
} from "./forecast";

type Editing = { scope: ScopeDeliveryResponse; title: string } | null;

/**
 * A project's committed delivery date and whether it will be met, with the
 * same for each of its pods' parts and its releases.
 *
 * The date is the product owner's or manager's to set; a pod's part is its
 * scrum master's, set from the pod's own panel; a release with no date of its
 * own uses its Jira release date.
 */
export function DeliveryForecastCard({ projectId, asOf }: { projectId: string; asOf: string }) {
  const role = useRole();
  // Nothing changes while a past day is viewed, so its date controls are hidden.
  const { isPast } = useViewingDate();
  const canSetProjectDates = role.canSetProjectDates && !isPast;
  const canSetPodDates = role.canSetPodDates && !isPast;
  const delivery = useQuery({
    queryKey: deliveryKey(projectId, asOf),
    queryFn: () => apiClient.projectDelivery(projectId, asOf),
  });
  const [editing, setEditing] = useState<Editing>(null);
  const [addingRelease, setAddingRelease] = useState(false);

  if (delivery.isError) {
    return (
      <Card padding="p-6">
        <h3 className="text-[18px] font-bold">Delivery date</h3>
        <p className="mt-2 text-[14px] text-rag-red">{errorMessage(delivery.error)}</p>
      </Card>
    );
  }
  if (!delivery.data) {
    return (
      <Card padding="p-6">
        <h3 className="text-[18px] font-bold">Delivery date</h3>
        <p className="mt-2 text-[14px] text-grey-secondary">Forecasting…</p>
      </Card>
    );
  }
  const { project, pods, releases } = delivery.data;
  const moved = movedLabel(project.commitment);

  return (
    <Card padding="p-6" className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-[18px] font-bold">Delivery date</h3>
            <RagChip tone={VERDICT_TONES[project.verdict]} dot>
              {VERDICT_LABELS[project.verdict]}
            </RagChip>
          </div>
          <div className="mt-2 flex items-baseline gap-3">
            <span className="text-[28px] font-extrabold">{dayLabel(project.target)}</span>
            <span className="text-[13px] text-grey-secondary">{targetSource(project)}</span>
          </div>
          {moved ? <p className="text-[13px] text-rag-amber">{moved}</p> : null}
        </div>
        {canSetProjectDates ? (
          <Pill
            variant="ghost"
            size="sm"
            onClick={() => setEditing({ scope: project, title: `Delivery date: ${project.name}` })}
          >
            <CalendarClock size={14} />
            {project.commitment.target_date ? "Change date" : "Set date"}
          </Pill>
        ) : null}
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <Forecast label="History says" value={historyLabel(project)} />
        <Forecast label="The team's dates" value={teamLabel(project)} />
      </div>

      {project.reasons.length > 0 ? (
        <ul className="flex flex-col gap-1 text-[13px] text-grey-body">
          {project.reasons.map((reason) => (
            <li key={reason}>• {reason}</li>
          ))}
        </ul>
      ) : null}

      {pods.length > 0 ? (
        <ScopeTable
          title="Pods"
          rows={pods}
          onEdit={
            canSetPodDates
              ? (scope) => setEditing({ scope, title: `${scope.name}'s date` })
              : undefined
          }
        />
      ) : null}

      <div>
        <ScopeTable
          title="Releases"
          rows={releases}
          empty="No releases yet. Add one from a Jira fix version or label to track its date."
          onEdit={
            canSetProjectDates
              ? (scope) => setEditing({ scope, title: `Delivery date: ${scope.name}` })
              : undefined
          }
          removable={canSetProjectDates ? projectId : undefined}
          asOf={asOf}
        />
        {canSetProjectDates ? (
          <Pill variant="ghost" size="sm" className="mt-3" onClick={() => setAddingRelease(true)}>
            <Plus size={14} />
            Add release
          </Pill>
        ) : null}
      </div>

      {editing ? (
        <DateDialog
          title={editing.title}
          scope={editing.scope}
          asOf={asOf}
          onClose={() => setEditing(null)}
        />
      ) : null}
      {addingRelease ? (
        <ReleaseDialog projectId={projectId} asOf={asOf} onClose={() => setAddingRelease(false)} />
      ) : null}
    </Card>
  );
}

function Forecast({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl bg-grey-fill px-4 py-3">
      <div className="text-[12px] font-bold uppercase tracking-wide text-grey-secondary">
        {label}
      </div>
      <div className="mt-1 text-[14px] font-bold">{value}</div>
    </div>
  );
}

function ScopeTable({
  title,
  rows,
  empty,
  onEdit,
  removable,
  asOf,
}: {
  title: string;
  rows: ScopeDeliveryResponse[];
  empty?: string;
  onEdit?: (scope: ScopeDeliveryResponse) => void;
  removable?: string;
  asOf?: string;
}) {
  const queryClient = useQueryClient();
  const remove = useMutation({
    mutationFn: (releaseId: string) => apiClient.removeRelease(removable ?? "", releaseId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["persona", "delivery"] });
      await queryClient.invalidateQueries({ queryKey: ["persona", "releases"] });
      toast.success("Release removed.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  return (
    <div>
      <h4 className="text-[15px] font-bold">{title}</h4>
      {rows.length === 0 ? (
        <p className="mt-1 text-[13px] text-grey-secondary">{empty}</p>
      ) : (
        <div className="mt-2 overflow-x-auto">
          <table className="w-full text-[14px]">
            <thead>
              <tr className="border-b border-grey-border text-left text-[12px] uppercase tracking-wide text-grey-secondary">
                <th className="py-2 pr-3 font-bold">{title === "Pods" ? "Pod" : "Release"}</th>
                <th className="px-3 py-2 font-bold">Date</th>
                <th className="px-3 py-2 font-bold">History says</th>
                <th className="px-3 py-2 font-bold">Open</th>
                <th className="px-3 py-2 font-bold">Verdict</th>
                <th className="py-2 pl-3" />
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.scope_id} className="border-b border-grey-border last:border-0">
                  <td className="py-2 pr-3 font-bold">{row.name}</td>
                  <td className="px-3 py-2 whitespace-nowrap">
                    {dayLabel(row.target)}
                    {row.target_source === "jira_release" ? (
                      <span className="ml-1 text-[12px] text-grey-secondary">(Jira)</span>
                    ) : null}
                  </td>
                  <td className="px-3 py-2 text-[13px]">{historyLabel(row)}</td>
                  <td className="px-3 py-2 tabular-nums">
                    {row.open} of {row.total}
                  </td>
                  <td className="px-3 py-2">
                    <RagChip tone={VERDICT_TONES[row.verdict]} className="h-6 text-[12px]">
                      {VERDICT_LABELS[row.verdict]}
                    </RagChip>
                  </td>
                  <td className="py-2 pl-3">
                    <div className="flex justify-end gap-1">
                      {onEdit ? (
                        <button
                          type="button"
                          aria-label={`Set the date of ${row.name}`}
                          className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
                          onClick={() => onEdit(row)}
                        >
                          <Pencil size={14} />
                        </button>
                      ) : null}
                      {removable && row.scope_kind === "release" ? (
                        <button
                          type="button"
                          aria-label={`Remove ${row.name}`}
                          className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
                          disabled={remove.isPending || !asOf}
                          onClick={() => remove.mutate(row.scope_id)}
                        >
                          <Trash2 size={14} />
                        </button>
                      ) : null}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function DateDialog({
  title,
  scope,
  asOf,
  onClose,
}: {
  title: string;
  scope: ScopeDeliveryResponse;
  asOf: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [target, setTarget] = useState(scope.commitment.target_date ?? "");
  const [note, setNote] = useState("");
  const save = useMutation({
    mutationFn: (body: DeliveryDateRequest) => {
      if (scope.scope_kind === "pod") {
        return apiClient.setPodDate(scope.project_id, scope.scope_id, body);
      }
      if (scope.scope_kind === "release") {
        return apiClient.setReleaseDate(scope.project_id, scope.scope_id, body);
      }
      return apiClient.setProjectDate(scope.project_id, body);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["persona", "delivery"] }),
        queryClient.invalidateQueries({ queryKey: ["persona", "pod-delivery"] }),
      ]);
      toast.success("Delivery date saved.");
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  return (
    <Modal open onOpenChange={(open) => (open ? undefined : onClose())} title={title}>
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          save.mutate({ target_date: target || null, note });
        }}
      >
        <FormField label="Delivery date" htmlFor="delivery-date">
          <TextInput
            id="delivery-date"
            type="date"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          />
        </FormField>
        <FormField label="Why (optional)" htmlFor="delivery-note">
          <TextInput
            id="delivery-note"
            value={note}
            maxLength={300}
            placeholder="Agreed with the business on the 3 Oct review"
            onChange={(event) => setNote(event.target.value)}
          />
        </FormField>
        {scope.commitment.changes.length > 0 ? (
          <div>
            <span className="text-[13px] font-bold text-grey-secondary">Earlier dates</span>
            <ul className="mt-1 flex flex-col gap-0.5 text-[13px]">
              {[...scope.commitment.changes].reverse().map((change) => (
                <li key={change.changed_at}>
                  {dayLabel(change.target_date)} · {change.changed_by_name} ·{" "}
                  {dayLabel(change.changed_at.slice(0, 10))}
                  {change.note ? (
                    <span className="text-grey-secondary"> · {change.note}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        <p className="text-[12px] text-grey-secondary">
          Every change is kept, so a date that moved shows as moved. Viewing {dayLabel(asOf)}.
        </p>
        <div className="flex justify-between gap-2">
          {scope.commitment.target_date ? (
            <Pill
              variant="ghost"
              size="sm"
              disabled={save.isPending}
              onClick={() => save.mutate({ target_date: null, note: note || "Date cleared" })}
            >
              Clear date
            </Pill>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Pill variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Pill>
            <Pill type="submit" size="sm" disabled={!target || save.isPending}>
              {save.isPending ? "Saving…" : "Save date"}
            </Pill>
          </div>
        </div>
      </form>
    </Modal>
  );
}

function ReleaseDialog({
  projectId,
  asOf,
  onClose,
}: {
  projectId: string;
  asOf: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const candidates = useQuery({
    queryKey: ["persona", "release-candidates", projectId],
    queryFn: () => apiClient.releaseCandidates(projectId),
  });
  const [kind, setKind] = useState<ReleaseMatchKind>("fix_version");
  const [value, setValue] = useState("");
  const [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () =>
      apiClient.createRelease(projectId, {
        name: name.trim() || value.trim(),
        match_kind: kind,
        match_value: value.trim(),
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: deliveryKey(projectId, asOf) }),
        queryClient.invalidateQueries({ queryKey: ["persona", "releases", projectId] }),
      ]);
      toast.success("Release added.");
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const offered = (candidates.data ?? []).filter((item) => item.kind === kind);
  return (
    <Modal open onOpenChange={(open) => (open ? undefined : onClose())} title="Add a release">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (value.trim()) create.mutate();
        }}
      >
        <FormField label="Defined by" htmlFor="release-kind">
          <AdminSelect
            id="release-kind"
            value={kind}
            onChange={(event) => {
              setKind(event.target.value as ReleaseMatchKind);
              setValue("");
            }}
          >
            <option value="fix_version">A Jira fix version</option>
            <option value="label">A Jira label</option>
          </AdminSelect>
        </FormField>
        <FormField label={kind === "fix_version" ? "Fix version" : "Label"} htmlFor="release-value">
          <AdminSelect
            id="release-value"
            value={value}
            onChange={(event) => {
              setValue(event.target.value);
              if (!name) setName(event.target.value);
            }}
          >
            <option value="">
              {candidates.isLoading
                ? "Loading…"
                : offered.length === 0
                  ? "None on this project's issues yet"
                  : "Pick one"}
            </option>
            {offered.map((item) => (
              <option key={item.value} value={item.value}>
                {item.value} · {item.issues} {item.issues === 1 ? "issue" : "issues"}
                {item.release_date ? ` · releases ${dayLabel(item.release_date)}` : ""}
              </option>
            ))}
          </AdminSelect>
        </FormField>
        <FormField label="Name" htmlFor="release-name">
          <TextInput
            id="release-name"
            value={name}
            placeholder="Checkout 1.0"
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
        <div className="flex justify-end gap-2">
          <Pill variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={!value.trim() || create.isPending}>
            {create.isPending ? "Adding…" : "Add release"}
          </Pill>
        </div>
      </form>
    </Modal>
  );
}
