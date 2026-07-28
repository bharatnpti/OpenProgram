import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { TextInput } from "../../components/ui/Field";
import type { ConfigNodeResponse, IdentityLinkUpdateRequest } from "../../api/schema";
import { blankToNull, errorMessage } from "./adminTypes";
import { FormField } from "./FormField";

export function IdentityLinkDialog({
  member,
  onClose,
}: {
  member: ConfigNodeResponse | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const memberId = member?.id ?? "";
  const [chatUserId, setChatUserId] = useState("");
  const [jiraAccountId, setJiraAccountId] = useState("");
  const [jiraEmail, setJiraEmail] = useState("");
  const [vcsUsername, setVcsUsername] = useState("");

  const link = useQuery({
    queryKey: ["config", "member-identity-link", memberId],
    queryFn: () => apiClient.configMemberIdentityLink(memberId),
    enabled: Boolean(member),
  });

  useEffect(() => {
    if (!link.data) return;
    setChatUserId(link.data.chat_user_id ?? "");
    setJiraAccountId(link.data.jira_account_id ?? "");
    setJiraEmail(link.data.jira_email ?? "");
    setVcsUsername(link.data.vcs_username ?? "");
  }, [link.data]);

  const saveMutation = useMutation({
    mutationFn: () => {
      const body: IdentityLinkUpdateRequest = {
        chat_user_id: blankToNull(chatUserId),
        jira_account_id: blankToNull(jiraAccountId),
        jira_email: blankToNull(jiraEmail),
        vcs_username: blankToNull(vcsUsername),
      };
      return apiClient.updateConfigMemberIdentityLink(memberId, body);
    },
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["config", "member-identity-link", memberId] }),
        queryClient.invalidateQueries({ queryKey: ["config", "unmapped-members"] }),
      ]);
      toast.success("Identity link saved.");
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <Modal
      open={Boolean(member)}
      onOpenChange={(open) => !open && onClose()}
      title={member ? `Identity link — ${member.name}` : "Identity link"}
    >
      <p className="mb-4 text-[13px] text-grey-secondary">
        Provider-neutral ids used to resolve this member across chat, Jira, and version control. A
        missing chat user id degrades check-in delivery.
      </p>
      {link.isLoading ? (
        <p className="text-[14px] text-grey-secondary">Loading…</p>
      ) : (
        <form
          className="flex flex-col gap-3.5"
          onSubmit={(event) => {
            event.preventDefault();
            saveMutation.mutate();
          }}
        >
          <div className="grid gap-3 md:grid-cols-2">
            <FormField label="Chat user ID" htmlFor="identity-chat-user-id">
              <TextInput
                id="identity-chat-user-id"
                value={chatUserId}
                onChange={(event) => setChatUserId(event.target.value)}
                placeholder="e.g. U1001"
              />
            </FormField>
            <FormField label="Jira account ID" htmlFor="identity-jira-account-id">
              <TextInput
                id="identity-jira-account-id"
                value={jiraAccountId}
                onChange={(event) => setJiraAccountId(event.target.value)}
              />
            </FormField>
            <FormField label="Jira email" htmlFor="identity-jira-email">
              <TextInput
                id="identity-jira-email"
                value={jiraEmail}
                onChange={(event) => setJiraEmail(event.target.value)}
                placeholder="name@example.com"
              />
            </FormField>
            <FormField label="VCS username" htmlFor="identity-vcs-username">
              <TextInput
                id="identity-vcs-username"
                value={vcsUsername}
                onChange={(event) => setVcsUsername(event.target.value)}
              />
            </FormField>
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
