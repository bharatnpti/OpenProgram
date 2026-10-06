import { useMutation, useQueries, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ConfigNodeResponse, EscalationContactDto } from "../../api/schema";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { refusal } from "./adminErrors";
import { FormDialog, fieldHelp, fieldInput, fieldLabel } from "./AdminDialog";
import {
  CONTACT_SLOTS,
  KEEP_SAVED,
  buildContactsUpdate,
  candidateLabel,
  contactName,
  contactState,
  groupCandidates,
  initialPicks,
  picksChanged,
  type ContactField,
  type Picks,
} from "./contactsForm";
import { useConfigList, useStructureChanged } from "./useStructure";

/**
 * Who a pod's waiting asks reach. When someone in a pod misses their check-in, the bot nudges
 * them first, then tells the pod's scrum master, then its manager. An ask that has waited past
 * the escalation matrix's days reaches the same two people for that pod's team. A pod with no
 * contact tells nobody above the person.
 */
export function ContactsTab() {
  const pods = useConfigList("pod");
  const list = [...(pods.data ?? [])].sort((a, b) => a.name.localeCompare(b.name));
  const contacts = useQueries({
    queries: list.map((pod) => ({
      queryKey: ["config", "pod-escalation-contacts", pod.id],
      queryFn: () => apiClient.podEscalationContacts(pod.id),
    })),
  });
  const [editing, setEditing] = useState<ConfigNodeResponse | null>(null);

  return (
    <>
      <p className="mb-4 max-w-[720px] text-[13px] text-grey-body">
        When someone in a pod misses their check-in, the bot nudges them, then tells the pod's scrum
        master, then its manager. An ask that has waited past the days in the escalation matrix
        reaches the same two people for that pod's team. A pod with no contact tells nobody above
        the person.
      </p>
      <PanelState
        needs="an admin"
        isLoading={pods.isLoading}
        error={pods.error}
        isEmpty={list.length === 0}
        emptyText="No pods yet. Add one under Entities first."
      >
        <TableBox>
          <table className="w-full min-w-[720px] border-collapse">
            <thead>
              <tr>
                <th className={th}>Pod</th>
                <th className={th}>Scrum master</th>
                <th className={th}>Manager</th>
                <th className={th}>Who is told</th>
                <th className={th} />
              </tr>
            </thead>
            <tbody>
              {list.map((pod, index) => {
                const query = contacts[index];
                const saved = query?.data;
                const state = contactState(saved);
                return (
                  <tr key={pod.id}>
                    <td className={td}>
                      <span className="font-bold">{pod.name}</span>
                      <span className="block font-mono text-[11px] text-grey-secondary">
                        {pod.id}
                      </span>
                    </td>
                    {CONTACT_SLOTS.map((slot) => (
                      <td key={slot.field} className={td}>
                        {query?.isLoading ? (
                          <span className="text-grey-secondary">…</span>
                        ) : (
                          <ContactCell contact={saved?.[slot.field]} />
                        )}
                      </td>
                    ))}
                    <td className={td}>
                      {query?.error ? (
                        <span className="text-[12px] text-rag-red">{refusal(query.error)}</span>
                      ) : saved ? (
                        <RagChip tone={state.tone} className="h-6 px-2.5 text-[12px]">
                          {state.label}
                        </RagChip>
                      ) : null}
                    </td>
                    <td className={`${td} text-right`}>
                      <Pill
                        size="sm"
                        variant="ghost"
                        className="h-8 px-3 text-[12px]"
                        aria-label={`Change the escalation contacts of ${pod.name}`}
                        onClick={() => setEditing(pod)}
                      >
                        Change
                      </Pill>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </TableBox>
      </PanelState>
      {editing ? <ContactsDialog pod={editing} onClose={() => setEditing(null)} /> : null}
    </>
  );
}

function ContactCell({ contact }: { contact: EscalationContactDto | null | undefined }) {
  const name = contactName(contact);
  if (!contact || !name) return <span className="text-grey-secondary">Nobody</span>;
  return (
    <>
      <span className="font-bold">{name}</span>
      {contact.member_id === null ? (
        <span className="block text-[12px] text-rag-amber">
          Saved as chat id {contact.chat_external_id}, not linked to a member
        </span>
      ) : null}
    </>
  );
}

function ContactsDialog({ pod, onClose }: { pod: ConfigNodeResponse; onClose: () => void }) {
  const changed = useStructureChanged();
  const saved = useQuery({
    queryKey: ["config", "pod-escalation-contacts", pod.id],
    queryFn: () => apiClient.podEscalationContacts(pod.id),
  });
  const candidates = useQuery({
    queryKey: ["config", "pod-escalation-candidates", pod.id],
    queryFn: () => apiClient.podEscalationCandidates(pod.id),
  });
  const loading = saved.isLoading || candidates.isLoading;
  const failed = saved.error ?? candidates.error;

  return (
    <FormDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={`Escalation contacts for ${pod.name}`}
      description="Who hears when someone in this pod misses a check-in or an ask waits too long: the scrum master first, then the manager."
    >
      {loading ? (
        <p className="mt-4 text-[14px] text-grey-secondary">Loading…</p>
      ) : failed ? (
        <p
          role="alert"
          className="mt-4 rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red"
        >
          Could not load this: {refusal(failed)}
        </p>
      ) : (
        <ContactsForm
          pod={pod}
          saved={saved.data}
          candidates={candidates.data ?? []}
          onSaved={async () => {
            await changed();
            onClose();
          }}
          onClose={onClose}
        />
      )}
    </FormDialog>
  );
}

function ContactsForm({
  pod,
  saved,
  candidates,
  onSaved,
  onClose,
}: {
  pod: ConfigNodeResponse;
  saved: Parameters<typeof initialPicks>[0];
  candidates: Parameters<typeof groupCandidates>[0];
  onSaved: () => Promise<void>;
  onClose: () => void;
}) {
  const [picks, setPicks] = useState<Picks>(() => initialPicks(saved));
  const [failure, setFailure] = useState<string | null>(null);
  const noChatId = candidates.filter((candidate) => !candidate.chat_user_id).length;

  const save = useMutation({
    mutationFn: () =>
      apiClient.updatePodEscalationContacts(pod.id, buildContactsUpdate(picks, saved)),
    onSuccess: async () => {
      toast.success(`Saved the escalation contacts for ${pod.name}.`);
      await onSaved();
    },
    onError: (error) => setFailure(refusal(error)),
  });

  const clearing =
    picks.scrum_master === "" && picks.manager === "" && (saved?.scrum_master || saved?.manager);

  return (
    <form
      className="mt-4 grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        setFailure(null);
        if (picksChanged(picks, saved)) save.mutate();
      }}
    >
      {CONTACT_SLOTS.map((slot) => (
        <ContactPicker
          key={slot.field}
          field={slot.field}
          label={slot.label}
          podRole={slot.podRole}
          saved={saved?.[slot.field]}
          candidates={candidates}
          value={picks[slot.field]}
          onChange={(value) => setPicks({ ...picks, [slot.field]: value })}
        />
      ))}
      {candidates.length === 0 ? (
        <p className={fieldHelp}>
          No members yet.{" "}
          <Link to="/admin?tab=directory" className="font-bold">
            Import people under Directory
          </Link>{" "}
          first.
        </p>
      ) : noChatId > 0 ? (
        <p className={fieldHelp}>
          {noChatId === 1 ? "One member has" : `${noChatId} members have`} no chat id yet, so can't
          be picked. Set it under{" "}
          <Link to="/admin?tab=directory" className="font-bold">
            Directory
          </Link>
          .
        </p>
      ) : null}
      {clearing ? (
        <p className="rounded-2xl bg-rag-amber-bg px-4 py-3 text-[13px] text-rag-amber-deep">
          With both left empty, nobody above the person is told when a check-in is missed in this
          pod.
        </p>
      ) : null}
      {failure ? (
        <p role="alert" className="rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red">
          {failure}
        </p>
      ) : null}
      <div className="flex justify-end gap-2">
        <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
          Cancel
        </Pill>
        <Pill type="submit" size="sm" disabled={!picksChanged(picks, saved) || save.isPending}>
          {save.isPending ? "Saving…" : "Save contacts"}
        </Pill>
      </div>
    </form>
  );
}

function ContactPicker({
  field,
  label,
  podRole,
  saved,
  candidates,
  value,
  onChange,
}: {
  field: ContactField;
  label: string;
  podRole: string;
  saved: EscalationContactDto | null | undefined;
  candidates: Parameters<typeof groupCandidates>[0];
  value: string;
  onChange: (value: string) => void;
}) {
  const { inPod, others } = groupCandidates(candidates, podRole);
  const unlinked = saved && !saved.member_id ? saved : null;
  const option = (candidate: (typeof candidates)[number]) => (
    <option
      key={candidate.member_id}
      value={candidate.member_id}
      disabled={!candidate.chat_user_id}
    >
      {candidateLabel(candidate)}
    </option>
  );
  const id = `contact-${field}`;
  return (
    <div>
      <label htmlFor={id} className={fieldLabel}>
        {label}
      </label>
      <select
        id={id}
        className={fieldInput}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">Nobody</option>
        {unlinked ? (
          <option value={KEEP_SAVED}>
            {contactName(unlinked)} · chat id {unlinked.chat_external_id}, not a member
          </option>
        ) : null}
        {inPod.length > 0 ? <optgroup label="In this pod">{inPod.map(option)}</optgroup> : null}
        {others.length > 0 ? (
          <optgroup label={inPod.length > 0 ? "Everyone else" : "Members"}>
            {others.map(option)}
          </optgroup>
        ) : null}
      </select>
      {value === KEEP_SAVED && unlinked ? (
        <p className="mt-1 text-[12px] font-bold text-rag-amber">
          Saved as chat id {unlinked.chat_external_id}, which matches no member. Pick a member to
          replace it.
        </p>
      ) : null}
    </div>
  );
}
