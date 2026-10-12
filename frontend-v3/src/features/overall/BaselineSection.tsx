import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { PlanningBaselineResponse, PlanningBaselinesResponse } from "../../api/schema";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { DayInput } from "../../components/ui/DayInput";
import { Pill } from "../../components/ui/Pill";
import { Fold } from "../../components/viz/Fold";
import { readState } from "../../lib/readState";
import { actionError } from "../reports/access";
import { FormProblem, Locked, ReportDialog, field, fieldLabel } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import {
  baselineHeadline,
  earlierLine,
  emptyPlanningForm,
  frozenWords,
  keyList,
  noBaselineWords,
  planningBody,
  planningProblem,
  sinceWords,
  sourceNote,
  type PlanningForm,
} from "./baselineWords";
import { useBaselines } from "./queries";
import { VizCard } from "./viz";

/**
 * The planning baseline of the project, or of the release picked above: the
 * requirements agreed when planning was complete, and how the scope moved since.
 * Read by the forecast's readers; Mark planning complete (and New baseline) only
 * for the people who commit the date (`setProjectDates`: product owner, manager,
 * admin), and not while a past day is shown. A new baseline keeps the earlier ones.
 */
export function BaselineSection({
  projectId,
  releaseId,
}: {
  projectId: string;
  releaseId: string;
}) {
  const access = useReportAccess();
  const baselines = useBaselines(projectId, releaseId || undefined);
  const [marking, setMarking] = useState(false);
  const data = baselines.query.data;
  const offNow = access.pastDay("setProjectDates");

  return (
    <section aria-labelledby="pb-h">
      <SectionHeader
        id="pb-h"
        title="Planning baseline"
        meta="The requirements agreed when planning was complete; what joins later is added scope"
        actions={
          data && access.setProjectDates ? (
            <Pill size="sm" variant="ghost" onClick={() => setMarking(true)}>
              {data.active ? "New baseline" : "Mark planning complete"}
            </Pill>
          ) : offNow ? (
            <Locked>{offNow}</Locked>
          ) : undefined
        }
      />
      <PanelState {...readState(baselines.query)} onRetry={() => void baselines.query.refetch()}>
        {data ? <BaselineCard read={data} /> : null}
      </PanelState>
      {marking && data ? (
        <PlanningCompleteDialog
          projectId={projectId}
          read={data}
          onClose={() => setMarking(false)}
        />
      ) : null}
    </section>
  );
}

function BaselineCard({ read }: { read: PlanningBaselinesResponse }) {
  const active = read.active;
  if (!active) {
    return (
      <VizCard>
        <p className="text-[13.5px] text-grey-body">{noBaselineWords(read)}</p>
      </VizCard>
    );
  }
  const earlier = read.baselines.filter((item) => item.baseline_id !== active.baseline_id);
  const first = earlier.length > 0 ? earlier[earlier.length - 1] : null;
  const note = sourceNote(active);

  return (
    <VizCard className="gap-2">
      <p className="text-[15px] font-extrabold text-ink">{baselineHeadline(active)}</p>
      <p className="text-[13.5px] text-grey-body">{frozenWords(active)}</p>
      <p className="text-[13.5px] text-grey-body">{sinceWords(active)}</p>
      {first ? (
        <p className="text-[13.5px] text-grey-body">
          {sinceWords(first, "Since the first baseline")}
        </p>
      ) : null}
      {active.note ? (
        <p className="text-[13px] text-grey-secondary">&ldquo;{active.note}&rdquo;</p>
      ) : null}
      {note ? <p className="text-[12.5px] text-grey-secondary">{note}</p> : null}
      <ChangedKeys baseline={active} />
      {earlier.length > 0 ? (
        <Fold summary={`Earlier baselines (${earlier.length})`} className="mt-1">
          <ul className="m-0 grid list-none gap-1 p-0 text-[13px] text-grey-body">
            {earlier.map((item) => (
              <li key={item.baseline_id}>{earlierLine(item)}</li>
            ))}
          </ul>
        </Fold>
      ) : null}
    </VizCard>
  );
}

/** The keys behind the counts, folded: added (marked with the label) and removed. */
function ChangedKeys({ baseline }: { baseline: PlanningBaselineResponse }) {
  const { since } = baseline;
  if (since.added === 0 && since.removed === 0) return null;
  return (
    <Fold summary="Which requirements" className="mt-1">
      <dl className="m-0 grid gap-1.5 text-[13px] text-grey-body">
        {since.added > 0 ? (
          <div>
            <dt className="inline font-bold text-ink">Added: </dt>
            <dd className="inline">
              {keyList(
                since.added_keys,
                since.added,
                since.added_with_label_keys,
                baseline.change_label,
              )}
            </dd>
          </div>
        ) : null}
        {since.removed > 0 ? (
          <div>
            <dt className="inline font-bold text-ink">Removed: </dt>
            <dd className="inline">{keyList(since.removed_keys, since.removed)}</dd>
          </div>
        ) : null}
      </dl>
    </Fold>
  );
}

/**
 * Mark planning complete for the scope: now, or on an earlier day (that day's
 * stored snapshot is frozen). Mounted while open, so each opening starts from
 * the defaults: the active baseline's label, else the server's.
 */
function PlanningCompleteDialog({
  projectId,
  read,
  onClose,
}: {
  projectId: string;
  read: PlanningBaselinesResponse;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<PlanningForm>(() =>
    emptyPlanningForm({
      label: read.active?.change_label ?? read.default_change_label,
      grace: read.active?.grace_working_days ?? read.default_grace_working_days,
    }),
  );
  const [tried, setTried] = useState(false);
  const today = new Date().toLocaleDateString("en-CA");
  const save = useMutation({
    mutationFn: () =>
      apiClient.markPlanningComplete(projectId, planningBody(form, read.release_id ?? "")),
    onSuccess: async (saved) => {
      await queryClient.invalidateQueries({ queryKey: ["baselines", projectId] });
      toast.success(
        `${read.name}: planning marked complete, ${saved.requirements} ${saved.requirements === 1 ? "requirement" : "requirements"} frozen.`,
      );
      onClose();
    },
  });
  const problem = tried ? planningProblem(form, today) : null;
  const set = (patch: Partial<PlanningForm>) => setForm((current) => ({ ...current, ...patch }));

  return (
    <ReportDialog
      open
      onOpenChange={(next) => (next ? undefined : onClose())}
      title={`Planning complete: ${read.name}`}
      description={
        read.active
          ? `Freezes the requirements in scope as baseline ${read.active.number + 1}, each with its stage, story points and due date. Baseline ${read.active.number} is kept. Nothing is sent.`
          : "Freezes the requirements in scope, each with its stage, story points and due date. Nothing is sent."
      }
    >
      <form
        className="grid grid-cols-[minmax(0,1fr)] gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setTried(true);
          if (planningProblem(form, today)) return;
          save.mutate();
        }}
      >
        <fieldset>
          <legend className={fieldLabel}>Planning was complete</legend>
          <div className="grid gap-2 text-[14px]">
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="planned-when"
                checked={form.when === "now"}
                onChange={() => set({ when: "now" })}
              />
              Now: the requirements as they stand
            </label>
            <label className="flex items-center gap-2">
              <input
                type="radio"
                name="planned-when"
                checked={form.when === "earlier"}
                onChange={() => set({ when: "earlier" })}
              />
              On an earlier day: that day&apos;s snapshot
            </label>
          </div>
          {form.when === "earlier" ? (
            <div className="mt-3">
              <DayInput
                aria-label="The day planning was complete"
                className={field}
                value={form.day}
                max={today}
                onChange={(day) => set({ day })}
              />
            </div>
          ) : null}
        </fieldset>
        <div>
          <label htmlFor="planned-label" className={fieldLabel}>
            Change label
          </label>
          <input
            id="planned-label"
            className={field}
            value={form.label}
            maxLength={64}
            onChange={(event) => set({ label: event.target.value })}
          />
          <p className="mt-1 text-[12px] text-grey-secondary">
            The Jira label a requirement added later carries when the change was agreed.
          </p>
        </div>
        <div>
          <label htmlFor="planned-grace" className={fieldLabel}>
            Grace period, in working days
          </label>
          <input
            id="planned-grace"
            className={field}
            inputMode="numeric"
            value={form.grace}
            onChange={(event) => set({ grace: event.target.value })}
          />
          <p className="mt-1 text-[12px] text-grey-secondary">
            A requirement entered this soon after planning still counts as planned.
          </p>
        </div>
        <div>
          <label htmlFor="planned-note" className={fieldLabel}>
            Note (optional)
          </label>
          <input
            id="planned-note"
            className={field}
            value={form.note}
            maxLength={300}
            placeholder="Scope agreed at the planning review"
            onChange={(event) => set({ note: event.target.value })}
          />
        </div>
        {problem ? <FormProblem>{problem}</FormProblem> : null}
        {save.error ? <FormProblem>{actionError(save.error)}</FormProblem> : null}
        <div className="flex flex-wrap justify-end gap-2">
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Mark planning complete"}
          </Pill>
        </div>
      </form>
    </ReportDialog>
  );
}
