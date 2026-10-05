import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, Plus, RotateCcw, X } from "lucide-react";
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
  parseNames,
  placementOf,
  requestFromDraft,
} from "./stageMapping";

const STAGES_QUERY_KEY = ["config", "delivery-stages"] as const;
const STATUSES_QUERY_KEY = ["config", "delivery-statuses"] as const;

/**
 * Which tracker statuses count as which of the six delivery stages.
 *
 * Lists every status the synced issues actually carry, so an admin places
 * real statuses rather than guessing names. A status no stage names is
 * counted by its broad state (to do, in progress, done) and flagged here.
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

  const unplaced = useMemo(
    () =>
      draft ? (observed.data ?? []).filter((item) => placementOf(draft, item.status) === null) : [],
    [draft, observed.data],
  );

  if (stages.isError) {
    return <p className="text-[14px] text-rag-red">{errorMessage(stages.error)}</p>;
  }
  if (!draft || !stages.data) {
    return (
      <Card padding="px-5 py-10" className="text-center text-[14px] text-grey-secondary">
        Loading delivery stages…
      </Card>
    );
  }

  const changed =
    JSON.stringify(requestFromDraft(draft)) !== JSON.stringify(savedRequest(stages.data));

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-bold">Delivery stages</h2>
          <p className="mt-1 max-w-[680px] text-[13px] text-grey-secondary">
            Place each tracker status in one of the six stages a requirement moves through. The
            requirements view and the day reports count by these stages.
            {stages.data.is_default ? " These are the defaults until you save." : ""}
          </p>
        </div>
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
          <Pill size="sm" disabled={!changed || save.isPending} onClick={() => save.mutate(draft)}>
            {save.isPending ? "Saving…" : "Save stages"}
          </Pill>
        </div>
      </div>

      {unplaced.length > 0 ? (
        <div
          role="status"
          className="flex items-start gap-2 rounded-2xl bg-rag-amber-bg px-4 py-3 text-[13px] text-rag-amber-deep"
        >
          <CircleAlert size={14} className="mt-0.5 shrink-0" />
          <span>
            {unplaced.length === 1 ? "One status is" : `${unplaced.length} statuses are`} counted by
            their broad state because no stage names them:{" "}
            <strong>{unplaced.map((item) => item.status).join(", ")}</strong>. Place them below.
          </span>
        </div>
      ) : null}

      <Card padding="p-0">
        <div className="px-5 pt-5 pb-3">
          <h3 className="text-[16px] font-bold">Statuses in your tracker</h3>
          <p className="mt-0.5 text-[13px] text-grey-secondary">
            Every status the synced issues carry, most common first.
          </p>
        </div>
        {observed.isError ? (
          <p className="px-5 pb-5 text-[13px] text-rag-red">{errorMessage(observed.error)}</p>
        ) : (observed.data ?? []).length === 0 ? (
          <p className="px-5 pb-5 text-[13px] text-grey-secondary">
            No synced issue carries a status yet. Run an issue sync, or add status names to the
            stages below.
          </p>
        ) : (
          <table className="w-full text-[14px]">
            <thead>
              <tr className="border-y border-grey-border bg-grey-header text-left text-[12px] uppercase tracking-wide text-grey-secondary">
                <th className="px-5 py-2 font-bold">Status</th>
                <th className="px-3 py-2 text-right font-bold">Issues</th>
                <th className="px-3 py-2 font-bold">Types</th>
                <th className="px-5 py-2 font-bold">Stage</th>
              </tr>
            </thead>
            <tbody>
              {(observed.data ?? []).map((item) => {
                const placement = placementOf(draft, item.status);
                return (
                  <tr key={item.status} className="border-b border-grey-border last:border-0">
                    <td className="px-5 py-2.5 font-bold">{item.status}</td>
                    <td className="px-3 py-2.5 text-right tabular-nums">{item.issues}</td>
                    <td className="px-3 py-2.5 text-[13px] text-grey-secondary">
                      {item.issue_types.join(", ") || "—"}
                    </td>
                    <td className="px-5 py-2">
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

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {STAGES.map((stage) => (
          <StageCard
            key={stage}
            stage={stage}
            names={draft.stages[stage] ?? []}
            onRemove={(name) => setDraft(moveStatus(draft, name, null))}
            onAdd={(name) => setDraft(moveStatus(draft, name, stage))}
          />
        ))}
        <StageCard
          stage="excluded"
          names={draft.excluded}
          onRemove={(name) => setDraft(moveStatus(draft, name, null))}
          onAdd={(name) => setDraft(moveStatus(draft, name, "excluded"))}
        />
      </div>

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
          Issue types that count as requirements, separated by commas. Leave it empty to count every
          issue type.
        </p>
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
      aria-label={`Stage for ${status}`}
      className={cn("py-2 text-[14px]", placement === null && "border-rag-amber")}
      value={placement ?? ""}
      onChange={(event) => {
        const value = event.target.value;
        onChange(value === "" ? null : (value as Placement));
      }}
    >
      <option value="">By its broad state (not placed)</option>
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
    <Card padding="p-4" className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <span
          aria-hidden
          className="inline-block h-3 w-3 rounded-[3px]"
          style={{
            backgroundColor: stage === "excluded" ? "var(--op-grey-disabled)" : STAGE_COLORS[stage],
          }}
        />
        <h3 className="text-[15px] font-bold">{label}</h3>
        <span className="text-[12px] text-grey-secondary">
          {names.length === 1 ? "1 status" : `${names.length} statuses`}
        </span>
      </div>
      <div className="flex flex-wrap gap-1.5">
        {names.length === 0 ? (
          <span className="text-[13px] text-grey-secondary">No status yet.</span>
        ) : (
          names.map((name) => (
            <span
              key={name}
              className="inline-flex h-8 items-center gap-1 rounded-full bg-grey-fill pl-3 pr-1.5 text-[13px] font-bold"
            >
              {name}
              <button
                type="button"
                aria-label={`Remove ${name} from ${label}`}
                className="rounded-full p-1 text-grey-secondary hover:bg-grey-hover hover:text-ink"
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
          aria-label={`Add a status to ${label}`}
          className="py-2 text-[14px]"
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
