import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { PanelState } from "../../components/PanelState";
import { cn } from "../../lib/utils";
import { trailLine } from "./requestWords";

/**
 * What happened to one ask, and what is due, numbered oldest first: asked
 * (and whether the DM went out), acknowledged (with the when its reply gave),
 * named in the day report by the escalation matrix, resolved or cleared. Only
 * what is on record or what the matrix makes due: a step that has not happened
 * is drawn open, and a step from the matrix says it is worked out. Read when the
 * row opens; the server leaves names out for a reader who reads none.
 */
export function RequestTrail({
  requestId,
  labelledBy,
}: {
  requestId: string;
  labelledBy?: string;
}) {
  const trail = useQuery({
    queryKey: ["requests", "trail", requestId],
    queryFn: () => apiClient.crossPersonRequestTrail(requestId),
  });
  const steps = trail.data?.steps ?? [];
  return (
    <PanelState
      isLoading={trail.isLoading}
      error={trail.error}
      onRetry={() => void trail.refetch()}
      isEmpty={trail.data !== undefined && steps.length === 0}
      emptyText="Nothing is on record for this ask yet."
    >
      <ol aria-labelledby={labelledBy} className="grid gap-3">
        {steps.map((step, index) => {
          const line = trailLine(step);
          return (
            <li key={`${step.kind}-${index}`} className="flex min-w-0 gap-3">
              <span
                aria-hidden
                className={cn(
                  "mt-0.5 inline-flex h-6 w-6 flex-none items-center justify-center rounded-full text-[12px] font-extrabold",
                  line.due
                    ? "border border-dashed border-grey-secondary text-grey-secondary"
                    : "bg-ink text-(--op-viz-surface)",
                )}
              >
                {index + 1}
              </span>
              <div className="min-w-0">
                <p
                  className={cn("text-[14px] font-bold", line.due ? "text-grey-body" : "text-ink")}
                >
                  {line.title}
                  {line.due ? <span className="sr-only"> (not happened yet)</span> : null}
                </p>
                {line.detail ? (
                  <p className="mt-0.5 text-[12px] text-grey-secondary">{line.detail}</p>
                ) : null}
              </div>
            </li>
          );
        })}
      </ol>
      <p className="mt-3 text-[12px] text-grey-secondary">
        No reminders are sent for requests yet: only a DM that did not go out is tried again.
      </p>
    </PanelState>
  );
}
