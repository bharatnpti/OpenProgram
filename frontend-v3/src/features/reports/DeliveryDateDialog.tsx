import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { DeliveryDateRequest, ScopeDeliveryResponse } from "../../api/schema";
import { Pill } from "../../components/ui/Pill";
import { formatDate } from "../../lib/format";
import { dateProblem } from "../overall/overallWords";
import { actionError } from "./access";
import { FormProblem, ReportDialog, field, fieldLabel } from "./ReportDialog";

/** The writes that change a forecast: every view of delivery and today's report. */
function useDeliveryRefresh() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["delivery"] }),
      queryClient.invalidateQueries({ queryKey: ["pod-delivery"] }),
      queryClient.invalidateQueries({ queryKey: ["day-reports"] }),
    ]);
}

/**
 * Commit, move or clear one scope's delivery date: the project's, a release's
 * or a pod's part. Every change is kept with who made it and why, so a date
 * that moved shows as moved. Mounted while open, so each opening starts from
 * the committed date.
 */
export function DeliveryDateDialog({
  scope,
  title,
  onClose,
}: {
  scope: ScopeDeliveryResponse;
  title: string;
  onClose: () => void;
}) {
  const refresh = useDeliveryRefresh();
  const current = scope.commitment.target_date ?? "";
  const [target, setTarget] = useState(current);
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const today = new Date().toLocaleDateString("en-CA");

  const save = useMutation({
    mutationFn: (body: DeliveryDateRequest) => {
      if (scope.scope_kind === "pod") {
        return apiClient.setPodDate(scope.project_id, scope.scope_id, body);
      }
      if (scope.scope_kind === "release") {
        return apiClient.setReleaseDate(scope.project_id, scope.scope_id, body);
      }
      return apiClient.setProjectDate(scope.project_id, body);
    },
    onSuccess: async (saved) => {
      await refresh();
      toast.success(
        saved.target_date
          ? `${scope.name}: delivery date ${formatDate(saved.target_date)}.`
          : `${scope.name}: delivery date cleared.`,
      );
      onClose();
    },
  });
  const problem = tried ? dateProblem(target, today) : null;

  return (
    <ReportDialog
      open
      onOpenChange={(next) => (next ? undefined : onClose())}
      title={title}
      description="Every change is kept with who made it and why, so a date that moved shows as moved. Nothing is sent when you save."
    >
      <form
        className="grid grid-cols-[minmax(0,1fr)] gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setTried(true);
          if (dateProblem(target, today)) return;
          save.mutate({ target_date: target, note: note.trim() });
        }}
      >
        <div>
          <label htmlFor="delivery-date" className={fieldLabel}>
            Delivery date
          </label>
          <input
            id="delivery-date"
            type="date"
            className={field}
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          />
        </div>
        <div>
          <label htmlFor="delivery-note" className={fieldLabel}>
            Why (optional)
          </label>
          <input
            id="delivery-note"
            className={field}
            value={note}
            maxLength={300}
            placeholder="Agreed with the business at the review"
            onChange={(event) => setNote(event.target.value)}
          />
        </div>
        {scope.commitment.changes.length > 0 ? (
          <div>
            <p className={fieldLabel}>Earlier dates</p>
            <ul className="grid gap-1 text-[13px] text-grey-body">
              {[...scope.commitment.changes].reverse().map((change) => (
                <li key={change.changed_at}>
                  {change.target_date ? formatDate(change.target_date) : "Cleared"} ·{" "}
                  {change.changed_by_name} · {formatDate(change.changed_at)}
                  {change.note ? (
                    <span className="text-grey-secondary"> · {change.note}</span>
                  ) : null}
                </li>
              ))}
            </ul>
          </div>
        ) : null}
        {problem ? <FormProblem>{problem}</FormProblem> : null}
        {save.error ? <FormProblem>{actionError(save.error)}</FormProblem> : null}
        <div className="flex flex-wrap items-center justify-between gap-2">
          {current ? (
            <Pill
              type="button"
              variant="ghost"
              size="sm"
              disabled={save.isPending}
              onClick={() =>
                save.mutate({ target_date: null, note: note.trim() || "Date cleared" })
              }
            >
              Clear date
            </Pill>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Pill>
            <Pill type="submit" size="sm" disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Save date"}
            </Pill>
          </div>
        </div>
      </form>
    </ReportDialog>
  );
}
