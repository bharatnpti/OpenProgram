import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import type { ConfigNodeResponse, WriteBackConsent } from "../../api/schema";
import { AdminSelect } from "./AdminSelect";
import { errorMessage } from "./adminTypes";
import { FormField } from "./FormField";

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
      <p className="mb-4 text-[13px] text-grey-secondary">
        Governs whether this member's check-in claims may update the issue tracker. Always-ask
        proposes the change and waits for a yes/no in the DM; auto-apply grants standing consent;
        never opts out. The tenant flag and capability must also be enabled.
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
