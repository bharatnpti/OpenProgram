import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, CircleAlert, Plus, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  DeliveryStage,
  DeliveryStagesResponse,
  ObservedStatusResponse,
} from "../../api/schema";
import { PanelState, TableBox, th } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { STAGE_LABELS, stageColor } from "../../lib/status";
import { cn } from "../../lib/utils";
import { TabIntro, hintClass, inputClass, labelClass } from "./AdminBits";
import { errorText } from "./adminWords";
import { savedLine, useMemberNames } from "./members";
import {
  NOT_COUNTED_MEANING,
  STEPS,
  STEP_MEANING,
  type Placement,
  type StageDraft,
  draftFromResponse,
  issueCount,
  issuesPerStep,
  moveStatus,
  otherNames,
  parseNames,
  placementOf,
  requestFromDraft,
  sameDraft,
  unplacedFirst,
} from "./stageMapping";

const STAGES_KEY = ["config", "delivery-stages"] as const;
const STATUSES_KEY = ["config", "delivery-statuses"] as const;
/** A table cell from md up; below it, a piece of the stacked row. */
const cell = "align-top text-[13px] md:border-b md:border-grey-border md:px-3 md:py-2.5";

/** The draft a moment after the last change, so typing does not ask the server every key. */
function useSettled<T>(value: T, delay = 400): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setSettled(value), delay);
    return () => window.clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

/**
 * Which tracker statuses count as which of the six delivery steps.
 *
 * Leads with the statuses the synced issues really carry, most issues first,
 * and with those no step names yet at the top: those are counted by their
 * broad state (to do, in progress, done) until someone places them. The names
 * other trackers use are folded away; they only place a status that appears
 * later.
 */
export function DeliveryStagesTab() {
  const stages = useQuery({ queryKey: STAGES_KEY, queryFn: () => apiClient.deliveryStages() });
  const observed = useQuery({
    queryKey: STATUSES_KEY,
    queryFn: () => apiClient.observedStatuses(),
  });

  return (
    <PanelState
      isLoading={stages.isLoading}
      error={stages.error}
      onRetry={() => void stages.refetch()}
    >
      {stages.data ? (
        <StagesEditor
          key={stages.data.updated_at ?? "default"}
          saved={stages.data}
          observed={observed.data ?? []}
          observedError={observed.error}
          observedLoading={observed.isLoading}
        />
      ) : null}
    </PanelState>
  );
}

function StagesEditor({
  saved,
  observed,
  observedError,
  observedLoading,
}: {
  saved: DeliveryStagesResponse;
  observed: ObservedStatusResponse[];
  observedError: unknown;
  observedLoading: boolean;
}) {
  const queryClient = useQueryClient();
  const nameOf = useMemberNames();
  const savedDraft = useMemo(() => draftFromResponse(saved), [saved]);
  const [draft, setDraft] = useState<StageDraft>(savedDraft);
  const [typesText, setTypesText] = useState(saved.requirement_types.join(", "));
  const changed = !sameDraft(draft, savedDraft);

  // Where the issues land under the draft: the server places them, so a
  // status nothing names is counted by its broad state exactly as it will be.
  const settled = useSettled(draft);
  const settledChanged = !sameDraft(settled, savedDraft);
  const preview = useQuery({
    queryKey: [...STATUSES_KEY, "preview", JSON.stringify(requestFromDraft(settled))],
    queryFn: () => apiClient.previewObservedStatuses(requestFromDraft(settled)),
    enabled: settledChanged,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
  const placed = settledChanged && preview.data ? preview.data : observed;
  const counts = issuesPerStep(placed);
  const placedBy = new Map(placed.map((row) => [row.status.trim().toLowerCase(), row]));

  // Ordered by what is saved, so a row stays put while its step is chosen.
  const rows = useMemo(() => unplacedFirst(observed, savedDraft), [observed, savedDraft]);
  const unplaced = rows.filter((row) => placementOf(draft, row.status) === null);
  const others = otherNames(
    draft,
    observed.map((row) => row.status),
  );

  const save = useMutation({
    mutationFn: () => apiClient.saveDeliveryStages(requestFromDraft(draft)),
    onSuccess: () => {
      toast.success("Delivery stages saved. Requirements are counted by them from now on.");
      void queryClient.invalidateQueries({ queryKey: STAGES_KEY });
      void queryClient.invalidateQueries({ queryKey: STATUSES_KEY });
      // Overall's requirements and gates, and the day report, count by these steps.
      void queryClient.invalidateQueries({ queryKey: ["requirements"] });
      void queryClient.invalidateQueries({ queryKey: ["gates"] });
      void queryClient.invalidateQueries({ queryKey: ["day-reports"] });
    },
    onError: (error) => toast.error(errorText(error)),
  });

  const savedText = savedLine(saved.updated_at, saved.updated_by, nameOf);

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
      <TabIntro>
        OpenProgram follows every requirement through six steps, from being asked for to being live.
        Your tracker names its statuses its own way, so choose below which step each of its statuses
        counts as. Overall, the day report and the gates all count by these steps.
      </TabIntro>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl bg-grey-fill px-4 py-3">
        <p className="text-[13px] text-grey-body">
          {changed
            ? "Unsaved changes. The counts below already show where issues would land."
            : saved.is_default
              ? "These are the defaults; nothing is saved for this tenant yet."
              : (savedText ?? "Saved.")}
        </p>
        <div className="flex gap-2">
          <Pill
            size="sm"
            variant="ghost"
            disabled={!changed || save.isPending}
            onClick={() => {
              setDraft(savedDraft);
              setTypesText(saved.requirement_types.join(", "));
            }}
          >
            Undo changes
          </Pill>
          <Pill size="sm" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Saving…" : "Save stages"}
          </Pill>
        </div>
      </div>

      <section aria-labelledby="steps-heading">
        <h3 id="steps-heading" className="mb-2 text-[17px] font-extrabold">
          The six steps
        </h3>
        <ol className="grid grid-cols-[minmax(0,1fr)] gap-2 sm:grid-cols-2 lg:grid-cols-3">
          {STEPS.map((step, index) => (
            <li key={step} className="rounded-2xl border border-grey-border p-4">
              <div className="flex items-center gap-2">
                <span
                  aria-hidden
                  className="inline-block h-3 w-3 flex-none rounded-[3px]"
                  style={{ backgroundColor: stageColor(step) }}
                />
                <span className="text-[15px] font-extrabold">
                  {index + 1}. {STAGE_LABELS[step]}
                </span>
                <span className="ml-auto text-[12px] font-bold tabular-nums text-grey-secondary">
                  {observedLoading ? "…" : issueCount(counts.steps[step])}
                </span>
              </div>
              <p className="mt-1 text-[13px] text-grey-body">{STEP_MEANING[step]}</p>
            </li>
          ))}
        </ol>
        <p className={hintClass}>
          <b>Not counted</b>: {NOT_COUNTED_MEANING}{" "}
          {counts.notCounted > 0 ? `${issueCount(counts.notCounted)} now.` : ""}
        </p>
      </section>

      <Panel
        title="Statuses in your tracker"
        note={
          unplaced.length > 0 ? (
            <span className="inline-flex items-center gap-1.5 rounded-full bg-rag-amber-bg px-3 py-1 text-[12px] font-bold text-rag-amber-deep">
              <CircleAlert size={13} aria-hidden />
              {unplaced.length === 1 ? "1 not placed yet" : `${unplaced.length} not placed yet`}
            </span>
          ) : undefined
        }
      >
        <p className="mb-3 text-[13px] text-grey-body">
          Every status your synced issues carry, those not placed yet first. Pick the step each one
          means; “Not counted” leaves it out of every total.
        </p>
        {observedError ? (
          <p className="rounded-2xl bg-rag-red-bg px-3 py-2 text-[13px] text-rag-red">
            Could not read the tracker's statuses: {errorText(observedError)}
          </p>
        ) : observedLoading ? (
          <p className="text-[13px] text-grey-secondary">Reading the tracker's statuses…</p>
        ) : rows.length === 0 ? (
          <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
            No synced issue carries a status yet. Once issues sync, each status appears here to
            place. Until then, the names under “Other status names” below decide.
          </p>
        ) : (
          <TableBox>
            {/* A table from md up; below it each row stacks, so "Counts as" stays in view. */}
            <table className="block w-full border-collapse md:table md:min-w-[640px]">
              <thead className="hidden md:table-header-group">
                <tr>
                  <th className={th}>Status</th>
                  <th className={`${th} text-right`}>Issues</th>
                  <th className={th}>Issue types</th>
                  <th className={th}>Counts as</th>
                </tr>
              </thead>
              <tbody className="block md:table-row-group">
                {rows.map((row) => {
                  const placement = placementOf(draft, row.status);
                  const fallback = placedBy.get(row.status.trim().toLowerCase());
                  return (
                    <tr
                      key={row.status}
                      className={cn(
                        "grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 border-b border-grey-border px-3 py-3 last:border-b-0 md:table-row md:p-0",
                        placement === null && "bg-rag-amber-bg/40",
                      )}
                    >
                      <td className={cn(cell, "min-w-0")}>
                        <span className="font-bold break-words">{row.status}</span>
                        {placement === null ? (
                          <span className="mt-0.5 flex items-start gap-1 text-[12px] text-rag-amber-deep">
                            <CircleAlert size={12} className="mt-0.5 flex-none" aria-hidden />
                            {fallback?.stage && !fallback.mapped
                              ? `Not placed yet: counted as ${STAGE_LABELS[fallback.stage]} by its broad state until you choose.`
                              : "Not placed yet: counted by its broad state until you choose."}
                          </span>
                        ) : null}
                      </td>
                      <td className={cn(cell, "text-right tabular-nums")}>
                        {row.issues}
                        <span className="text-grey-secondary md:hidden">
                          {row.issues === 1 ? " issue" : " issues"}
                        </span>
                      </td>
                      <td className={cn(cell, "col-span-2 text-grey-secondary")}>
                        {row.issue_types.join(", ") || <span className="hidden md:inline">—</span>}
                      </td>
                      <td className={cn(cell, "col-span-2 pt-1 md:w-[260px]")}>
                        <PlacementSelect
                          status={row.status}
                          placement={placement}
                          onChange={(target) =>
                            setDraft((current) => moveStatus(current, row.status, target))
                          }
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableBox>
        )}
      </Panel>

      <Panel title="Which issues are requirements">
        <label htmlFor="requirement-types" className={labelClass}>
          Issue types that count
        </label>
        <input
          id="requirement-types"
          className={inputClass}
          value={typesText}
          placeholder="Every type counts. Story, Epic…"
          onChange={(event) => {
            setTypesText(event.target.value);
            const requirementTypes = parseNames(event.target.value);
            setDraft((current) => ({ ...current, requirementTypes }));
          }}
        />
        <p className={hintClass}>
          Only issues of these types are counted as requirements. Separate them with commas, or
          leave it empty to count every type.
        </p>
      </Panel>

      <details className="group rounded-3xl border border-grey-border">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-4 [&::-webkit-details-marker]:hidden">
          <ChevronRight
            aria-hidden
            size={16}
            className="flex-none text-grey-secondary transition-transform group-open:rotate-90"
          />
          <span className="text-[15px] font-bold">
            Other status names OpenProgram recognises ({others.total})
          </span>
        </summary>
        <div className="grid gap-3 px-5 pb-5">
          <p className="text-[13px] text-grey-body">
            Names other trackers give these steps. A status with one of these names that appears in
            your tracker later is placed on its own; a status your tracker already uses is chosen in
            the table above.
          </p>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3 md:grid-cols-2 xl:grid-cols-3">
            {STEPS.map((step) => (
              <NamesCard
                key={step}
                stage={step}
                names={others.stages[step]}
                onRemove={(name) => setDraft((current) => moveStatus(current, name, null))}
                onAdd={(name) => setDraft((current) => moveStatus(current, name, step))}
              />
            ))}
            <NamesCard
              stage="excluded"
              names={others.excluded}
              onRemove={(name) => setDraft((current) => moveStatus(current, name, null))}
              onAdd={(name) => setDraft((current) => moveStatus(current, name, "excluded"))}
            />
          </div>
        </div>
      </details>
    </div>
  );
}

function PlacementSelect({
  status,
  placement,
  onChange,
}: {
  status: string;
  placement: Placement;
  onChange: (target: Placement) => void;
}) {
  return (
    <select
      aria-label={`What ${status} counts as`}
      className={cn(inputClass, placement === null && "border-rag-amber")}
      value={placement ?? ""}
      onChange={(event) => {
        const value = event.target.value;
        onChange(value === "" ? null : (value as Placement));
      }}
    >
      <option value="">Not placed yet</option>
      {STEPS.map((step, index) => (
        <option key={step} value={step}>
          {index + 1}. {STAGE_LABELS[step]}
        </option>
      ))}
      <option value="excluded">Not counted</option>
    </select>
  );
}

function NamesCard({
  stage,
  names,
  onRemove,
  onAdd,
}: {
  stage: DeliveryStage | "excluded";
  names: string[];
  onRemove: (name: string) => void;
  onAdd: (name: string) => void;
}) {
  const [adding, setAdding] = useState("");
  const label = stage === "excluded" ? "Not counted" : STAGE_LABELS[stage];
  return (
    <div className="grid min-w-0 content-start gap-3 rounded-2xl bg-grey-fill p-4">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className="inline-block h-3 w-3 flex-none rounded-[3px]"
          style={{
            backgroundColor: stage === "excluded" ? "var(--op-grey-disabled)" : stageColor(stage),
          }}
        />
        <h4 className="text-[14px] font-bold">{label}</h4>
        <span className="text-[12px] text-grey-secondary">
          {names.length === 1 ? "1 name" : `${names.length} names`}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {names.length === 0 ? (
          <span className="text-[13px] text-grey-secondary">No other names.</span>
        ) : (
          names.map((name) => (
            <span
              key={name}
              className="inline-flex h-8 max-w-full items-center gap-1 rounded-full bg-white pl-3 pr-1.5 text-[13px] font-bold"
            >
              <span className="truncate">{name}</span>
              <button
                type="button"
                aria-label={`Remove ${name} from ${label}`}
                className="flex-none rounded-full p-1 text-grey-secondary hover:bg-grey-hover hover:text-ink"
                onClick={() => onRemove(name)}
              >
                <X size={12} aria-hidden />
              </button>
            </span>
          ))
        )}
      </div>
      <form
        className="flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          if (adding.trim()) {
            onAdd(adding);
            setAdding("");
          }
        }}
      >
        <input
          aria-label={`Add a status name to ${label}`}
          className={cn(inputClass, "h-9")}
          value={adding}
          maxLength={120}
          placeholder="Add a status name"
          onChange={(event) => setAdding(event.target.value)}
        />
        <Pill type="submit" variant="ghost" size="sm" disabled={!adding.trim()}>
          <Plus size={14} aria-hidden />
          Add
        </Pill>
      </form>
    </div>
  );
}
