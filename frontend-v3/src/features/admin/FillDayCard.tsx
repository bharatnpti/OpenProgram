import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { useReadOnly } from "../../app/viewingDate";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { hintClass } from "./AdminBits";
import { errorText } from "./adminWords";
import {
  FILL_DAY_KEY,
  FILL_DAY_LABEL,
  fillDayLine,
  fillDaySaved,
  fillDaySource,
} from "./fillDayWords";
import { useMembers } from "./members";

/**
 * Admin › Check-ins: "Fill the day from Jira for people who were not asked". On
 * by default; off, the people the check-in does not reach keep no status for the
 * day. The deployment's own switch wins when it is off, and the box says so.
 */
export function FillDayCard() {
  const queryClient = useQueryClient();
  const members = useMembers();
  const { readOnly, reason } = useReadOnly();
  const fill = useQuery({ queryKey: FILL_DAY_KEY, queryFn: () => apiClient.configCheckinFill() });
  const save = useMutation({
    mutationFn: (enabled: boolean) => apiClient.saveConfigCheckinFill({ enabled }),
    onSuccess: (saved) => {
      queryClient.setQueryData(FILL_DAY_KEY, saved);
      toast.success(fillDaySaved(saved.enabled));
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const data = fill.data;
  const nameOf = (id: string) => members.data?.find((m) => m.id === id)?.name ?? null;

  return (
    <section
      aria-labelledby="fill-day-label"
      className="grid gap-2 rounded-3xl border border-grey-border bg-white p-5"
    >
      <div className="flex items-start gap-3">
        <input
          id="fill-day"
          type="checkbox"
          role="switch"
          aria-checked={data?.enabled ?? false}
          aria-describedby="fill-day-line"
          checked={data?.enabled ?? false}
          disabled={!data || save.isPending || readOnly || !data.deployment_enabled}
          title={readOnly ? (reason ?? undefined) : undefined}
          className="mt-1 h-4 w-4 flex-none"
          onChange={(event) => save.mutate(event.target.checked)}
        />
        <label htmlFor="fill-day" className="min-w-0">
          <span id="fill-day-label" className="block text-[15px] font-extrabold">
            {FILL_DAY_LABEL}
          </span>
          {data ? (
            <span className="mt-1 flex flex-wrap items-center gap-2">
              <RagChip
                tone={data.enabled && data.deployment_enabled ? "success" : "neutral"}
                className="h-6 px-2.5 text-[12px]"
              >
                {data.enabled && data.deployment_enabled ? "On" : "Off"}
              </RagChip>
              <span className="text-[12px] text-grey-secondary">
                {fillDaySource(data, { day: formatDay, name: nameOf })}
              </span>
            </span>
          ) : null}
        </label>
      </div>
      <p id="fill-day-line" className="text-[13px] text-grey-body">
        {data
          ? fillDayLine(data)
          : fill.isError
            ? `Whether days are filled from Jira is not known right now. ${errorText(fill.error)}`
            : "Reading whether days are filled from Jira…"}
      </p>
      <p className={hintClass}>
        Someone asked in chat keeps their own close-out: their reply, or a day inferred when they
        stay silent.
      </p>
    </section>
  );
}
