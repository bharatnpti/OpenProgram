import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";
import { PenLine, ShieldAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import type {
  ConfigNodeResponse,
  TenantWritebackResponse,
  WriteBackConsent,
} from "../../api/schema";
import { AdminSelect } from "./AdminSelect";
import { errorMessage } from "./adminTypes";
import { FormField } from "./FormField";
import { useTenantWriteback } from "./useTenantWriteback";

const WRITEBACK_CONSENT_OPTIONS: { value: WriteBackConsent; label: string }[] = [
  { value: "always_ask", label: "Always ask (default)" },
  { value: "auto_apply", label: "Auto-apply" },
  { value: "never", label: "Never" },
];

export function WritebackConsentDialog({
  member,
  onClose,
}: {
  member: ConfigNodeResponse | null;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const memberId = member?.id ?? "";
  const [consent, setConsent] = useState<WriteBackConsent>("always_ask");
  const gate = useTenantWriteback(Boolean(member));

  const preference = useQuery({
    queryKey: ["config", "member-writeback-consent", memberId],
    queryFn: () => apiClient.configMemberWritebackConsent(memberId),
    enabled: Boolean(member),
  });

  useEffect(() => {
    if (!preference.data) return;
    setConsent(preference.data.consent);
  }, [preference.data]);

  const saveMutation = useMutation({
    mutationFn: () => apiClient.updateConfigMemberWritebackConsent(memberId, { consent }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["config", "member-writeback-consent", memberId],
      });
      toast.success("Write-back consent saved.");
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <Modal
      open={Boolean(member)}
      onOpenChange={(open) => !open && onClose()}
      title={member ? `Write-back consent — ${member.name}` : "Write-back consent"}
    >
      <WritebackGateNotice gate={gate} />
      <p className="mb-4 text-[13px] text-grey-secondary">
        Decides whether this member's check-in claims may update Jira. Always ask proposes the
        change in the DM and waits for a yes or no. Auto-apply makes the change without asking.
        Never opts out.
      </p>
      {preference.isLoading ? (
        <p className="text-[14px] text-grey-secondary">Loading…</p>
      ) : (
        <form
          className="flex flex-col gap-3.5"
          onSubmit={(event) => {
            event.preventDefault();
            saveMutation.mutate();
          }}
        >
          <FormField label="Consent preference" htmlFor="writeback-consent">
            <AdminSelect
              id="writeback-consent"
              value={consent}
              onChange={(event) => setConsent(event.target.value as WriteBackConsent)}
            >
              {WRITEBACK_CONSENT_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </AdminSelect>
          </FormField>
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

// The members table opens consent from here, so a closed switch shows on the row
// itself. "On" and still-loading carry no suffix; only a known-off or unknown state does.
export function WritebackConsentButton({ onClick }: { onClick: () => void }) {
  const gate = useTenantWriteback();
  const suffix = gate.isError ? "unknown" : gate.data && !gate.data.enabled ? "off" : null;
  const title = gate.isError
    ? "Couldn't check whether write-back is on."
    : suffix === "off"
      ? "Write-back is off for this workspace. Consent is saved but nothing is written to Jira."
      : undefined;

  return (
    <Pill variant="ghost" size="sm" onClick={onClick} title={title}>
      <PenLine size={14} />
      Write-back
      {suffix ? <span className="font-normal text-grey-secondary">{suffix}</span> : null}
    </Pill>
  );
}

function WritebackGateNotice({ gate }: { gate: UseQueryResult<TenantWritebackResponse, Error> }) {
  if (gate.isError) {
    return (
      <div role="status" className="mb-4 rounded-2xl bg-rag-unknown-bg px-4 py-3 text-[13px]">
        <p className="font-bold">Couldn't check whether write-back is on.</p>
        <p className="mt-1 text-grey-body">
          Consent you save is kept either way. It only takes effect while write-back is on for this
          workspace.
        </p>
      </div>
    );
  }
  if (gate.isPending) {
    return (
      <p className="mb-4 text-[13px] text-grey-secondary">Checking whether write-back is on…</p>
    );
  }
  if (gate.data.enabled) {
    return (
      <p className="mb-4 text-[13px] font-bold text-rag-green">
        Write-back is on for this workspace.
      </p>
    );
  }
  return (
    <div
      role="status"
      className="mb-4 flex gap-3 rounded-2xl bg-rag-amber-bg px-4 py-3 text-[13px] text-ink"
    >
      <ShieldAlert size={16} className="mt-0.5 shrink-0 text-rag-amber" />
      <div>
        <p className="font-bold">Write-back is off for this workspace.</p>
        <p className="mt-1">
          Nothing is written to Jira, whatever consent says. You can still set consent now. It takes
          effect once write-back is switched on.
        </p>
        <p className="mt-1">
          {gate.data.source === "tenant" ? (
            <>
              It was switched off for this workspace, which overrides the deployment default.
              Whoever runs OpenProgram can switch it back on with{" "}
              <code className="font-mono">PUT /config/tenant/writeback</code>.
            </>
          ) : (
            <>
              It is off by default. Whoever runs OpenProgram can switch it on for every workspace
              with <code className="font-mono">OPENPROGRAM_JIRA_WRITEBACK_ENABLED=true</code>, or
              for this workspace only with{" "}
              <code className="font-mono">PUT /config/tenant/writeback</code>.
            </>
          )}
        </p>
      </div>
    </div>
  );
}
