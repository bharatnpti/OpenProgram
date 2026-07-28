import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { TextInput } from "../../components/ui/Field";
import type { ConfigNodeResponse, PodEscalationContactsUpdateRequest } from "../../api/schema";
import { errorMessage } from "./adminTypes";
import { FormField } from "./FormField";

function contactPayload(chatId: string, displayName: string) {
  const id = chatId.trim();
  if (!id) return null;
  const name = displayName.trim();
  return { chat_external_id: id, display_name: name ? name : null };
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
  const [smId, setSmId] = useState("");
  const [smName, setSmName] = useState("");
  const [managerId, setManagerId] = useState("");
  const [managerName, setManagerName] = useState("");

  const contacts = useQuery({
    queryKey: ["config", "pod-escalation-contacts", podId],
    queryFn: () => apiClient.podEscalationContacts(podId),
    enabled: Boolean(pod),
  });

  useEffect(() => {
    if (!contacts.data) return;
    setSmId(contacts.data.scrum_master?.chat_external_id ?? "");
    setSmName(contacts.data.scrum_master?.display_name ?? "");
    setManagerId(contacts.data.manager?.chat_external_id ?? "");
    setManagerName(contacts.data.manager?.display_name ?? "");
  }, [contacts.data]);

  const saveMutation = useMutation({
    mutationFn: () => {
      const body: PodEscalationContactsUpdateRequest = {
        scrum_master: contactPayload(smId, smName),
        manager: contactPayload(managerId, managerName),
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

  return (
    <Modal
      open={Boolean(pod)}
      onOpenChange={(open) => !open && onClose()}
      title={pod ? `Escalation contacts — ${pod.name}` : "Escalation contacts"}
    >
      <p className="mb-4 text-[13px] text-grey-secondary">
        Scrum master and manager targets for this pod's check-in escalation ladder. Clear a chat
        ID to remove that contact.
      </p>
      {contacts.isLoading ? (
        <p className="text-[14px] text-grey-secondary">Loading…</p>
      ) : (
        <form
          className="flex flex-col gap-3.5"
          onSubmit={(event) => {
            event.preventDefault();
            saveMutation.mutate();
          }}
        >
          <div className="flex flex-col gap-3 rounded-2xl bg-grey-fill p-4">
            <div className="text-[14px] font-bold">Scrum master</div>
            <div className="grid gap-3 md:grid-cols-2">
              <FormField label="Chat ID" htmlFor="escalation-sm-id">
                <TextInput
                  id="escalation-sm-id"
                  value={smId}
                  onChange={(event) => setSmId(event.target.value)}
                  placeholder="e.g. U1001"
                />
              </FormField>
              <FormField label="Display name" htmlFor="escalation-sm-name">
                <TextInput
                  id="escalation-sm-name"
                  value={smName}
                  onChange={(event) => setSmName(event.target.value)}
                />
              </FormField>
            </div>
          </div>
          <div className="flex flex-col gap-3 rounded-2xl bg-grey-fill p-4">
            <div className="text-[14px] font-bold">Manager</div>
            <div className="grid gap-3 md:grid-cols-2">
              <FormField label="Chat ID" htmlFor="escalation-manager-id">
                <TextInput
                  id="escalation-manager-id"
                  value={managerId}
                  onChange={(event) => setManagerId(event.target.value)}
                  placeholder="e.g. U1002"
                />
              </FormField>
              <FormField label="Display name" htmlFor="escalation-manager-name">
                <TextInput
                  id="escalation-manager-name"
                  value={managerName}
                  onChange={(event) => setManagerName(event.target.value)}
                />
              </FormField>
            </div>
          </div>
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
