import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import type {
  ConfigNodeResponse,
  EscalationCandidateResponse,
  EscalationContactDto,
  EscalationContactUpdateDto,
  PodEscalationContactsUpdateRequest,
} from "../../api/schema";
import { AdminSelect } from "./AdminSelect";
import { errorMessage } from "./adminTypes";
import { FormField } from "./FormField";

// Picker value for a saved contact whose chat ID matches no member: keep it as it is.
const KEEP_SAVED = "__saved__";

function initialPick(contact: EscalationContactDto | null | undefined) {
  if (!contact) return "";
  return contact.member_id ?? KEEP_SAVED;
}

function contactChoice(
  pick: string,
  saved: EscalationContactDto | null | undefined,
): EscalationContactUpdateDto | null {
  if (!pick) return null;
  if (pick === KEEP_SAVED) return saved ? { chat_external_id: saved.chat_external_id } : null;
  return { member_id: pick };
}

function roleLabel(role: string) {
  const words = role.replace(/_/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function candidateLabel(candidate: EscalationCandidateResponse) {
  const parts = [candidate.name];
  if (candidate.in_pod && candidate.pod_role) parts.push(roleLabel(candidate.pod_role));
  if (!candidate.chat_user_id) parts.push("No chat ID linked");
  return parts.join(" · ");
}

function savedLabel(contact: EscalationContactDto) {
  return contact.display_name
    ? `${contact.display_name} (${contact.chat_external_id})`
    : contact.chat_external_id;
}

function ContactPicker({
  id,
  label,
  role,
  saved,
  candidates,
  value,
  onChange,
}: {
  id: string;
  label: string;
  role: string;
  saved: EscalationContactDto | null | undefined;
  candidates: EscalationCandidateResponse[];
  value: string;
  onChange: (value: string) => void;
}) {
  // Pod members holding this role come first, then the rest of the pod.
  const podMembers = candidates
    .filter((candidate) => candidate.in_pod)
    .sort((a, b) => Number(a.pod_role !== role) - Number(b.pod_role !== role));
  const others = candidates.filter((candidate) => !candidate.in_pod);
  const unlinkedSaved = saved && !saved.member_id ? saved : null;

  return (
    <FormField label={label} htmlFor={id}>
      <AdminSelect id={id} value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">No contact</option>
        {unlinkedSaved ? (
          <option value={KEEP_SAVED}>{savedLabel(unlinkedSaved)} · Not linked to a member</option>
        ) : null}
        {podMembers.length > 0 ? (
          <optgroup label="This pod">
            {podMembers.map((candidate) => (
              <option
                key={candidate.member_id}
                value={candidate.member_id}
                disabled={!candidate.chat_user_id}
              >
                {candidateLabel(candidate)}
              </option>
            ))}
          </optgroup>
        ) : null}
        {others.length > 0 ? (
          <optgroup label={podMembers.length > 0 ? "Everyone else" : "Members"}>
            {others.map((candidate) => (
              <option
                key={candidate.member_id}
                value={candidate.member_id}
                disabled={!candidate.chat_user_id}
              >
                {candidateLabel(candidate)}
              </option>
            ))}
          </optgroup>
        ) : null}
      </AdminSelect>
      {value === KEEP_SAVED && unlinkedSaved ? (
        <p className="mt-1.5 flex items-start gap-1.5 text-[12px] font-medium text-rag-amber">
          <ShieldAlert size={14} className="mt-px shrink-0" />
          Not linked to a member. Saved as chat ID {unlinkedSaved.chat_external_id}. Pick a member
          to replace it.
        </p>
      ) : null}
    </FormField>
  );
}

export function EscalationContactsDialog({
  pod,
  onClose,
}: {
  pod: ConfigNodeResponse | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const podId = pod?.id ?? "";
  const [smPick, setSmPick] = useState("");
  const [managerPick, setManagerPick] = useState("");

  const contacts = useQuery({
    queryKey: ["config", "pod-escalation-contacts", podId],
    queryFn: () => apiClient.podEscalationContacts(podId),
    enabled: Boolean(pod),
  });
  const candidates = useQuery({
    queryKey: ["config", "pod-escalation-candidates", podId],
    queryFn: () => apiClient.podEscalationCandidates(podId),
    enabled: Boolean(pod),
  });

  useEffect(() => {
    if (!contacts.data) return;
    setSmPick(initialPick(contacts.data.scrum_master));
    setManagerPick(initialPick(contacts.data.manager));
  }, [contacts.data]);

  const saveMutation = useMutation({
    mutationFn: () => {
      const body: PodEscalationContactsUpdateRequest = {
        scrum_master: contactChoice(smPick, contacts.data?.scrum_master),
        manager: contactChoice(managerPick, contacts.data?.manager),
      };
      return apiClient.updatePodEscalationContacts(podId, body);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["config", "pod-escalation-contacts", podId],
      });
      toast.success("Escalation contacts saved.");
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const members = candidates.data ?? [];
  const unlinkedCount = members.filter((candidate) => !candidate.chat_user_id).length;

  return (
    <Modal
      open={Boolean(pod)}
      onOpenChange={(open) => !open && onClose()}
      title={pod ? `Escalation contacts — ${pod.name}` : "Escalation contacts"}
    >
      <p className="mb-4 text-[13px] text-grey-secondary">
        Who hears when a developer misses their check-in: the scrum master first, then the manager.
        Pick members. Their chat ID comes from their identity link.
      </p>
      {contacts.isLoading || candidates.isLoading ? (
        <p className="text-[14px] text-grey-secondary">Loading…</p>
      ) : contacts.isError || candidates.isError ? (
        <p className="text-[14px] text-rag-red">
          {errorMessage(contacts.error ?? candidates.error)}
        </p>
      ) : (
        <form
          className="flex flex-col gap-3.5"
          onSubmit={(event) => {
            event.preventDefault();
            saveMutation.mutate();
          }}
        >
          <ContactPicker
            id="escalation-sm"
            label="Scrum master"
            role="scrum_master"
            saved={contacts.data?.scrum_master}
            candidates={members}
            value={smPick}
            onChange={setSmPick}
          />
          <ContactPicker
            id="escalation-manager"
            label="Manager"
            role="manager"
            saved={contacts.data?.manager}
            candidates={members}
            value={managerPick}
            onChange={setManagerPick}
          />
          {members.length === 0 ? (
            <p className="text-[12px] text-grey-secondary">
              No members yet. Add members before picking contacts.
            </p>
          ) : unlinkedCount > 0 ? (
            <p className="text-[12px] text-grey-secondary">
              Members with no chat ID linked can't be picked. Set one with Identity on the Members
              list.
            </p>
          ) : null}
          <div className="mt-1 flex justify-end gap-3">
            <Pill variant="ghost" size="md" type="button" onClick={onClose}>
              Cancel
            </Pill>
            <Pill variant="primary" size="md" type="submit" disabled={saveMutation.isPending}>
              {saveMutation.isPending ? "Saving…" : "Save"}
            </Pill>
          </div>
        </form>
      )}
    </Modal>
  );
}
