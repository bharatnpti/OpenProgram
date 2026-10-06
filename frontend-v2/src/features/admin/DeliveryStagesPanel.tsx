import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, CircleAlert, Plus, RotateCcw, X } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { DeliveryStage, DeliveryStagesResponse } from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { TextInput } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import { cn } from "../../lib/utils";
import { STAGE_COLORS, STAGE_LABELS, STAGES } from "../requirements/stages";
import { AdminSelect } from "./AdminSelect";
import { FormField } from "./FormField";
import { errorMessage } from "./adminTypes";
import {
  type Placement,
  type StageDraft,
  draftFromResponse,
  moveStatus,
  otherNames,
  parseNames,
  placementOf,
  requestFromDraft,
  unplacedFirst,
} from "./stageMapping";

const STAGES_QUERY_KEY = ["config", "delivery-stages"] as const;
const STATUSES_QUERY_KEY = ["config", "delivery-statuses"] as const;

const UNPLACED_NOTE = "Not placed yet: counted by its broad state until you choose a step";

/**
 * Which tracker statuses count as which of the six delivery steps.
 *
 * Leads with every status the synced issues actually carry, so an admin places
 * real statuses rather than guessing names; one no step names is counted by
 * its broad state (to do, in progress, done), flagged and listed first. The
 * names other trackers use stay folded away: they only place a status the
 * tracker starts using later.
 */
export function DeliveryStagesPanel() {
  const queryClient = useQueryClient();
  const stages = useQuery({ queryKey: STAGES_QUERY_KEY, queryFn: apiClient.deliveryStages });
  const observed = useQuery({ queryKey: STATUSES_QUERY_KEY, queryFn: apiClient.observedStatuses });
  const [draft, setDraft] = useState<StageDraft | null>(null);
  const [typesText, setTypesText] = useState("");

  useEffect(() => {
    if (!stages.data) return;
    setDraft(draftFromResponse(stages.data));
    setTypesText(stages.data.requirement_types.join(", "));
  }, [stages.data]);

  const save = useMutation({
    mutationFn: (next: StageDraft) => apiClient.saveDeliveryStages(requestFromDraft(next)),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: STAGES_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: STATUSES_QUERY_KEY }),
        queryClient.invalidateQueries({ queryKey: ["persona", "requirements"] }),
      ]);
      toast.success("Delivery stages saved.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  // Ordered by what is saved, not by the draft, so a row stays put while its
  // step is being chosen and moves only once the choice is saved.
  const rows = useMemo(
    () => (stages.data ? unplacedFirst(observed.data ?? [], draftFromResponse(stages.data)) : []),
    [stages.data, observed.data],
  );
  const unplaced = useMemo(
    () => (draft ? rows.filter((item) => placementOf(draft, item.status) === null) : []),
    [draft, rows],
  );
  const others = useMemo(
    () =>
      draft
        ? otherNames(
            draft,
            (observed.data ?? []).map((item) => item.status),
          )
        : null,
    [draft, observed.data],
  );

  if (stages.isError) {
    return <p className="text-[14px] text-rag-red">{errorMessage(stages.error)}</p>;
  }
  if (!draft || !others || !stages.data) {
    return (
      <Card padding="px-5 py-10" className="text-center text-[14px] text-grey-secondary">
        Loading delivery stages…
      </Card>
    );
  }

  const changed =
    JSON.stringify(requestFromDraft(draft)) !== JSON.stringify(savedRequest(stages.data));

  return (
    <div className="flex min-w-0 flex-col gap-5">
      <div>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-[18px] font-bold">Delivery stages</h2>
          <div className="flex gap-2">
            <Pill
              variant="ghost"
              size="sm"
              disabled={!changed}
              onClick={() => {
                setDraft(draftFromResponse(stages.data));
                setTypesText(stages.data.requirement_types.join(", "));
              }}
            >
              <RotateCcw size={14} />
              Undo changes
            </Pill>
            <Pill
              size="sm"
              disabled={!changed || save.isPending}
              onClick={() => save.mutate(draft)}
            >
              {save.isPending ? "Saving…" : "Save stages"}
            </Pill>
          </div>
        </div>
        <p className="mt-2 max-w-[720px] text-[14px]">
          OpenProgram follows every requirement through six steps. Your tracker names its statuses
          its own way, so choose below which step each status counts as.
        </p>
        <ol aria-label="The six steps" className="mt-3 flex flex-wrap items-center gap-1.5">
          {STAGES.map((stage, index) => (
            <li key={stage} className="flex items-center gap-1.5">
              <span className="inline-flex h-8 items-center gap-2 whitespace-nowrap rounded-full bg-grey-fill px-3 text-[13px] font-bold">
                <span
                  aria-hidden
                  className="inline-block h-2.5 w-2.5 shrink-0 rounded-[3px]"
                  style={{ backgroundColor: STAGE_COLORS[stage] }}
                />
                {STAGE_LABELS[stage]}
              </span>
              {index < STAGES.length - 1 ? (
                <ChevronRight aria-hidden size={14} className="text-grey-secondary" />
              ) : null}
            </li>
          ))}
        </ol>
        <p className="mt-3 max-w-[720px] text-[13px] text-grey-secondary">
          The Requirements card and its timeline, the day report's “Where we stand” and the gates
          (business acceptance before Production, for example) all count by these steps.
          {stages.data.is_default ? " These are the defaults until you save." : ""}
        </p>
      </div>

      <Card padding="p-0" className="overflow-hidden">
        <div className="flex flex-wrap items-start justify-between gap-2 px-5 pt-5 pb-3">
          <div>
            <h3 className="text-[16px] font-bold">Statuses in your tracker</h3>
            <p className="mt-0.5 text-[13px] text-grey-secondary">
              Every status your synced issues carry, most common first. “Not counted” leaves a
              status out of the totals.
            </p>
          </div>
          {unplaced.length > 0 ? (
            <span
              role="status"
              className="inline-flex items-center gap-1.5 rounded-full bg-rag-amber-bg px-3 py-1 text-[12px] font-bold text-rag-amber-deep"
            >
              <CircleAlert size={13} className="shrink-0" />
              {unplaced.length === 1
                ? "1 status not placed yet"
                : `${unplaced.length} statuses not placed yet`}
            </span>
          ) : null}
        </div>
        {observed.isError ? (
          <p className="px-5 pb-5 text-[13px] text-rag-red">{errorMessage(observed.error)}</p>
        ) : rows.length === 0 ? (
          <p className="px-5 pb-5 text-[13px] text-grey-secondary">
            No synced issue carries a status yet. Run an issue sync, or add names under “Other
            status names OpenProgram recognises” below.
          </p>
        ) : (
          <table className="block w-full text-[14px] md:table">
            <thead className="hidden md:table-header-group">
              <tr className="border-y border-grey-border bg-grey-header text-left text-[12px] uppercase tracking-wide text-grey-secondary">
                <th className="px-5 py-2 font-bold">Status</th>
                <th className="px-3 py-2 text-right font-bold">Issues</th>
                <th className="px-3 py-2 font-bold">Issue types</th>
                <th className="px-5 py-2 font-bold">Counts as</th>
              </tr>
            </thead>
            <tbody className="block border-t border-grey-border md:table-row-group md:border-0">
              {rows.map((item) => {
                const placement = placementOf(draft, item.status);
                return (
                  <tr
                    key={item.status}
                    className={cn(
                      "grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-1 border-b border-grey-border px-5 py-3 last:border-0 md:table-row md:p-0",
                      placement === null && "bg-rag-amber-bg/40",
                    )}
                  >
                    <td className="min-w-0 md:px-5 md:py-2.5">
                      <span className="font-bold break-words">{item.status}</span>
                      {placement === null ? (
                        <span className="mt-0.5 flex items-start gap-1 text-[12px] font-medium text-rag-amber-deep">
                          <CircleAlert size={12} className="mt-0.5 shrink-0" />
                          {UNPLACED_NOTE}
                        </span>
                      ) : null}
                    </td>
                    <td className="text-right tabular-nums md:px-3 md:py-2.5">
                      {item.issues}
                      <span className="text-grey-secondary md:hidden">
                        {item.issues === 1 ? " issue" : " issues"}
                      </span>
                    </td>
                    <td className="col-span-2 text-[13px] text-grey-secondary md:px-3 md:py-2.5">
                      {item.issue_types.join(", ") || "—"}
                    </td>
                    <td className="col-span-2 pt-1 md:w-[300px] md:px-5 md:py-2">
                      <PlacementSelect
                        status={item.status}
                        placement={placement}
                        onChange={(target) => setDraft(moveStatus(draft, item.status, target))}
                      />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>

      <Card padding="p-5">
        <FormField label="Requirement types" htmlFor="requirement-types">
          <TextInput
            id="requirement-types"
            value={typesText}
            placeholder="Story, Epic, Requirement"
            onChange={(event) => {
              setTypesText(event.target.value);
              setDraft({ ...draft, requirementTypes: parseNames(event.target.value) });
            }}
          />
        </FormField>
        <p className="mt-1 text-[12px] text-grey-secondary">
          Only issues of these types count as requirements; issues of any other type are left out.
          Separate types with commas, or leave it empty to count every type.
        </p>
      </Card>

      <Card padding="p-0">
        <details className="group">
          <summary className="flex cursor-pointer list-none items-center gap-2 rounded-3xl px-5 py-4 [&::-webkit-details-marker]:hidden">
            <ChevronRight
              aria-hidden
              size={16}
              className="shrink-0 text-grey-secondary transition-transform group-open:rotate-90"
            />
            <span className="text-[15px] font-bold">
              Other status names OpenProgram recognises ({others.total})
            </span>
          </summary>
          <div className="flex flex-col gap-3 px-5 pb-5">
            <p className="text-[13px] text-grey-secondary">
              Names other trackers give these steps. If a status with one of these names appears in
              your tracker, it is placed automatically; a status your tracker already uses is chosen
              in the table above.
            </p>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {STAGES.map((stage) => (
                <StageCard
                  key={stage}
                  stage={stage}
                  names={others.stages[stage] ?? []}
                  onRemove={(name) => setDraft(moveStatus(draft, name, null))}
                  onAdd={(name) => setDraft(moveStatus(draft, name, stage))}
                />
              ))}
              <StageCard
                stage="excluded"
                names={others.excluded}
                hint="Statuses that drop out of the totals altogether, such as Won't Do or Cancelled."
                onRemove={(name) => setDraft(moveStatus(draft, name, null))}
                onAdd={(name) => setDraft(moveStatus(draft, name, "excluded"))}
              />
            </div>
          </div>
        </details>
      </Card>
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
    <AdminSelect
      aria-label={`What ${status} counts as`}
      className={cn("py-2 text-[14px]", placement === null && "border-rag-amber")}
      value={placement ?? ""}
      onChange={(event) => {
        const value = event.target.value;
        onChange(value === "" ? null : (value as Placement));
      }}
    >
      <option value="">Not placed yet (by its broad state)</option>
      {STAGES.map((stage) => (
        <option key={stage} value={stage}>
          {STAGE_LABELS[stage]}
        </option>
      ))}
      <option value="excluded">Not counted</option>
    </AdminSelect>
  );
}

function StageCard({
  stage,
  names,
  hint,
  onRemove,
  onAdd,
}: {
  stage: DeliveryStage | "excluded";
  names: string[];
  hint?: string;
  onRemove: (name: string) => void;
  onAdd: (name: string) => void;
}) {
  const [adding, setAdding] = useState("");
  const label = stage === "excluded" ? "Not counted" : STAGE_LABELS[stage];
  return (
    <Card variant="grey" padding="p-4" className="flex min-w-0 flex-col gap-3">
      <div>
        <div className="flex items-center gap-2">
          <span
            aria-hidden
            className="inline-block h-3 w-3 shrink-0 rounded-[3px]"
            style={{
              backgroundColor:
                stage === "excluded" ? "var(--op-grey-disabled)" : STAGE_COLORS[stage],
            }}
          />
          <h3 className="text-[15px] font-bold">{label}</h3>
          <span className="text-[12px] text-grey-secondary">
            {names.length === 1 ? "1 name" : `${names.length} names`}
          </span>
        </div>
        {hint ? <p className="mt-1 text-[12px] text-grey-secondary">{hint}</p> : null}
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
                className="shrink-0 rounded-full p-1 text-grey-secondary hover:bg-grey-hover hover:text-ink"
                onClick={() => onRemove(name)}
              >
                <X size={12} />
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
        <TextInput
          aria-label={`Add a status name to ${label}`}
          className="min-w-0 py-2 text-[14px]"
          value={adding}
          placeholder="Add a status name"
          onChange={(event) => setAdding(event.target.value)}
        />
        <Pill type="submit" variant="ghost" size="sm" disabled={!adding.trim()}>
          <Plus size={14} />
          Add
        </Pill>
      </form>
    </Card>
  );
}

function savedRequest(response: DeliveryStagesResponse) {
  return requestFromDraft(draftFromResponse(response));
}
