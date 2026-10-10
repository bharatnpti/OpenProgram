import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { ConfigNodeResponse } from "../../api/schema";
import { Pill } from "../../components/ui/Pill";
import { refusal } from "./adminErrors";
import { FormDialog, fieldError, fieldHelp, fieldInput, fieldLabel } from "./AdminDialog";
import {
  IDENTITY_FIELDS,
  buildIdentityUpdate,
  formFromLink,
  validateIdentity,
  type IdentityForm,
} from "./identityForm";
import { useStructureChanged } from "./useStructure";

/**
 * Which chat, Jira and Git accounts are this member. The chat id is how the bot reaches them;
 * the Jira account is how their issues are found; the Git username is how their commits are.
 * Only the ids that change are sent, so a blank clears one and the rest stay as they are.
 */
export function IdentityDialog({
  member,
  onClose,
}: {
  member: ConfigNodeResponse;
  onClose: () => void;
}) {
  const link = useQuery({
    queryKey: ["config", "member-identity-link", member.id],
    queryFn: () => apiClient.configMemberIdentityLink(member.id),
  });
  return (
    <FormDialog
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={`Accounts for ${member.name}`}
      description="Which chat, Jira and Git accounts are this person. They are how the console reaches them and finds their work."
    >
      {link.isLoading ? (
        <p className="mt-4 text-[14px] text-grey-secondary">Loading…</p>
      ) : link.error ? (
        <p
          role="alert"
          className="mt-4 rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red"
        >
          Could not load their accounts: {refusal(link.error)}
        </p>
      ) : (
        <AccountsForm member={member} saved={link.data ?? null} onClose={onClose} />
      )}
    </FormDialog>
  );
}

function AccountsForm({
  member,
  saved,
  onClose,
}: {
  member: ConfigNodeResponse;
  saved: Parameters<typeof formFromLink>[0];
  onClose: () => void;
}) {
  const changed = useStructureChanged();
  const [form, setForm] = useState<IdentityForm>(() => formFromLink(saved));
  const [failure, setFailure] = useState<string | null>(null);
  const errors = validateIdentity(form);
  const update = buildIdentityUpdate(saved, form);
  const ready = Object.keys(update).length > 0 && Object.keys(errors).length === 0;

  const save = useMutation({
    mutationFn: () => apiClient.updateConfigMemberIdentityLink(member.id, update),
    onSuccess: async () => {
      toast.success(`Saved the accounts for ${member.name}.`);
      await changed();
      onClose();
    },
    onError: (error) => setFailure(refusal(error)),
  });

  return (
    <form
      className="mt-4 grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        setFailure(null);
        if (ready) save.mutate();
      }}
    >
      {IDENTITY_FIELDS.map(({ form: key, label, help, placeholder }) => (
        <div key={key}>
          <label htmlFor={`identity-${key}`} className={fieldLabel}>
            {label}
          </label>
          <input
            id={`identity-${key}`}
            className={fieldInput}
            placeholder={placeholder}
            autoComplete="off"
            value={form[key]}
            onChange={(event) => setForm({ ...form, [key]: event.target.value })}
          />
          {errors[key] ? (
            <p role="alert" className={fieldError}>
              {errors[key]}
            </p>
          ) : (
            <p className={fieldHelp}>{help}</p>
          )}
        </div>
      ))}
      {form.chatUserId.trim() === "" ? (
        <p className="rounded-2xl bg-rag-amber-bg px-4 py-3 text-[13px] text-rag-amber-deep">
          Without a chat id {member.name} is never asked to check in and cannot be an escalation
          contact.
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
        <Pill type="submit" size="sm" disabled={!ready || save.isPending}>
          {save.isPending ? "Saving…" : "Save accounts"}
        </Pill>
      </div>
    </form>
  );
}
