import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { useReadOnly } from "../../app/viewingDate";
import { Pill } from "../../components/ui/Pill";
import { ConfirmChange, type Pending } from "./AdminDialog";
import { askNowWords } from "./askNowWords";

/**
 * Ask now: an admin asks one member for today's check-in at once, as the
 * scheduled one does (`POST /admin/workflows/checkin/dispatch`), with a yes
 * first. Only the tenant and the member are sent: the backend finds where the
 * message goes, so it can never reach someone outside the team. Off on a past
 * day, like every change.
 */
export function AskNow({ memberId, name }: { memberId: string; name: string }) {
  const queryClient = useQueryClient();
  const { readOnly, reason } = useReadOnly();
  const [pending, setPending] = useState<Pending | null>(null);
  const words = askNowWords(name);
  return (
    <>
      <Pill
        size="sm"
        variant="ghost"
        disabled={readOnly}
        title={readOnly ? (reason ?? undefined) : undefined}
        aria-label={words.button}
        onClick={() =>
          setPending({
            key: `ask:${memberId}:${Date.now()}`,
            title: words.title,
            effect: words.effect,
            names: [name],
            confirmLabel: "Ask now",
            destructive: false,
            run: async () => {
              const health = await apiClient.health();
              await apiClient.dispatchCheckin({
                tenant_id: health.tenant_id,
                developer_id: memberId,
              });
              toast.success(words.done);
              void queryClient.invalidateQueries({ queryKey: ["pod"] });
              void queryClient.invalidateQueries({ queryKey: ["me"] });
            },
          })
        }
      >
        Ask now
      </Pill>
      <ConfirmChange pending={pending} onClose={() => setPending(null)} />
    </>
  );
}
