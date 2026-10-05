import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, RotateCcw, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  ConfigNodeResponse,
  ContactSource,
  EscalationMatrixResponse,
  NeedType,
} from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { TextInput } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { AdminSelect, NodeSelect } from "./AdminSelect";
import { ConfirmDialog } from "./ConfirmDialog";
import { FormField } from "./FormField";
import { type ConfirmState, errorMessage } from "./adminTypes";
import {
  MATRIX_SOURCE_LABELS,
  MAX_LEVELS,
  NEEDS,
  NEED_HINTS,
  NEED_LABELS,
  SOURCES,
  SOURCE_LABELS,
  type LevelDraft,
  type MatrixDraft,
  draftFromMatrix,
  draftProblems,
  newLevel,
  requestFromDraft,
} from "./escalationForm";

const OVERVIEW_KEY = ["config", "escalation"] as const;
const TENANT = "";

/**
 * After how long each kind of ask in a day report goes up a level, and who
 * stands at each level.
 *
 * The tenant's matrix is what every project uses until it is given its own;
 * a project's own can be dropped to go back to the tenant's.
 */
export function EscalationPanel({
  projects,
  members,
}: {
  projects: ConfigNodeResponse[];
  members: ConfigNodeResponse[];
}) {
  const [scope, setScope] = useState(TENANT);
  const overview = useQuery({ queryKey: OVERVIEW_KEY, queryFn: apiClient.escalationOverview });
  const project = useQuery({
    queryKey: [...OVERVIEW_KEY, "project", scope],
    queryFn: () => apiClient.projectEscalation(scope),
    enabled: scope !== TENANT,
  });
  const own = new Set((overview.data?.projects ?? []).map((item) => item.project_id));

  if (overview.isError) {
    return <p className="text-[14px] text-rag-red">{errorMessage(overview.error)}</p>;
  }
  const matrix = scope === TENANT ? overview.data?.tenant : project.data;

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-[18px] font-bold">Escalation</h2>
        <p className="mt-1 max-w-[700px] text-[13px] text-grey-secondary">
          Everything a day report lists under &ldquo;What we need, and from whom&rdquo; is an ask: a
          fix, a decision, an answer or a review, owned by the person who can do it. The longer an
          ask waits, the higher it goes. Each level says after how many days each kind of ask
          reaches it, and who stands there.
        </p>
      </div>

      <Card padding="p-5" className="flex flex-wrap items-end gap-4">
        <div className="min-w-[240px] flex-1">
          <FormField label="Matrix for" htmlFor="escalation-scope">
            <AdminSelect
              id="escalation-scope"
              value={scope}
              onChange={(event) => setScope(event.target.value)}
            >
              <option value={TENANT}>Every project without its own</option>
              {projects.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                  {own.has(item.id) ? " (its own)" : ""}
                </option>
              ))}
            </AdminSelect>
          </FormField>
        </div>
        {matrix ? (
          <RagChip tone={matrix.source === "default" ? "neutral" : "info"} className="mb-2">
            {MATRIX_SOURCE_LABELS[matrix.source]}
            {scope !== TENANT && matrix.source !== "project" ? ", inherited" : ""}
          </RagChip>
        ) : null}
      </Card>

      {!matrix ? (
        <Card padding="px-5 py-10" className="text-center text-[14px] text-grey-secondary">
          Loading the matrix…
        </Card>
      ) : (
        <MatrixEditor
          key={`${scope}:${matrix.source}:${matrix.updated_at ?? ""}`}
          scope={scope}
          matrix={matrix}
          members={members}
        />
      )}
    </div>
  );
}

function MatrixEditor({
  scope,
  matrix,
  members,
}: {
  scope: string;
  matrix: EscalationMatrixResponse;
  members: ConfigNodeResponse[];
}) {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState<MatrixDraft>(() => draftFromMatrix(matrix));
  const [attempted, setAttempted] = useState(false);
  const [confirm, setConfirm] = useState<ConfirmState>({ open: false });

  const inherited = scope !== TENANT && matrix.source !== "project";
  const invalidate = () => queryClient.invalidateQueries({ queryKey: OVERVIEW_KEY });
  const save = useMutation({
    mutationFn: () =>
      scope === TENANT
        ? apiClient.saveTenantEscalation(requestFromDraft(draft))
        : apiClient.saveProjectEscalation(scope, requestFromDraft(draft)),
    onSuccess: async () => {
      await invalidate();
      toast.success("Escalation saved.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const reset = useMutation({
    mutationFn: () => apiClient.removeProjectEscalation(scope),
    onSuccess: async () => {
      await invalidate();
      toast.success("The project uses the tenant's matrix again.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const problems = draftProblems(draft);
  const changed =
    JSON.stringify(requestFromDraft(draft)) !==
    JSON.stringify(requestFromDraft(draftFromMatrix(matrix)));
  const update = (index: number, change: (level: LevelDraft) => LevelDraft) =>
    setDraft((current) => ({
      ...current,
      levels: current.levels.map((level, at) => (at === index ? change(level) : level)),
    }));

  return (
    <div className="flex flex-col gap-4">
      {inherited ? (
        <p className="text-[13px] text-grey-secondary">
          This project uses the {matrix.source === "tenant" ? "tenant's" : "default"} matrix. Save a
          change here to give it its own.
        </p>
      ) : null}

      <Card padding="p-5">
        <FormField label="Who decides" htmlFor="escalation-decider">
          <NodeSelect
            id="escalation-decider"
            items={members}
            value={draft.decisionOwnerId}
            placeholder="Nobody named yet"
            onChange={(decisionOwnerId) => setDraft((current) => ({ ...current, decisionOwnerId }))}
          />
        </FormField>
        <p className="mt-1 text-[12px] text-grey-secondary">
          The product owner, usually: decisions such as accepting a requirement go to them when
          nothing else names an owner.
        </p>
      </Card>

      <Card padding="p-5" className="flex flex-col gap-3">
        <div>
          <h3 className="text-[16px] font-bold">Levels</h3>
          <p className="text-[13px] text-grey-secondary">
            Level 1 is always the ask&apos;s owner. A day left empty means that kind never reaches
            the level. The highest level an ask reached is named in the report.
          </p>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] text-[14px]">
            <thead>
              <tr className="border-b border-grey-border text-left text-[12px] uppercase tracking-wide text-grey-secondary">
                <th className="py-2 pr-3 font-bold">Level</th>
                <th className="px-2 py-2 font-bold">Goes to</th>
                {NEEDS.map((need) => (
                  <th key={need} className="px-2 py-2 font-bold" title={NEED_HINTS[need]}>
                    {NEED_LABELS[need]}
                    <span className="block text-[11px] font-normal normal-case tracking-normal">
                      after days
                    </span>
                  </th>
                ))}
                <th className="py-2 pl-2" />
              </tr>
            </thead>
            <tbody>
              {draft.levels.map((level, index) => (
                <tr key={index} className="border-b border-grey-border align-top last:border-0">
                  <td className="py-2 pr-3">
                    <span className="mb-1 block text-[12px] font-bold text-grey-secondary">
                      {index + 2}
                    </span>
                    <TextInput
                      aria-label={`Level ${index + 2} name`}
                      className="py-2 text-[14px]"
                      value={level.label}
                      maxLength={60}
                      placeholder="Delivery lead"
                      onChange={(event) => {
                        const label = event.target.value;
                        update(index, (current) => ({ ...current, label }));
                      }}
                    />
                  </td>
                  <td className="px-2 py-2">
                    <span className="mb-1 block text-[12px]">&nbsp;</span>
                    <AdminSelect
                      aria-label={`Level ${index + 2} goes to`}
                      className="py-2 text-[14px]"
                      value={level.source}
                      onChange={(event) => {
                        const source = event.target.value as ContactSource;
                        update(index, (current) => ({ ...current, source }));
                      }}
                    >
                      {SOURCES.map((source) => (
                        <option key={source} value={source}>
                          {SOURCE_LABELS[source]}
                        </option>
                      ))}
                    </AdminSelect>
                    {level.source === "member" ? (
                      <div className="mt-2">
                        <NodeSelect
                          id={`escalation-member-${index}`}
                          items={members}
                          value={level.memberId}
                          placeholder="Pick a member"
                          onChange={(memberId) =>
                            update(index, (current) => ({ ...current, memberId }))
                          }
                        />
                      </div>
                    ) : null}
                  </td>
                  {NEEDS.map((need) => (
                    <td key={need} className="px-2 py-2">
                      <span className="mb-1 block text-[12px]">&nbsp;</span>
                      <DaysInput
                        need={need}
                        level={index + 2}
                        value={level.days[need]}
                        onChange={(value) =>
                          update(index, (current) => ({
                            ...current,
                            days: { ...current.days, [need]: value },
                          }))
                        }
                      />
                    </td>
                  ))}
                  <td className="py-2 pl-2">
                    <span className="mb-1 block text-[12px]">&nbsp;</span>
                    <button
                      type="button"
                      aria-label={`Remove level ${index + 2}`}
                      className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
                      onClick={() =>
                        setDraft((current) => ({
                          ...current,
                          levels: current.levels.filter((_, at) => at !== index),
                        }))
                      }
                    >
                      <X size={16} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {draft.levels.length === 0 ? (
          <p className="text-[13px] text-grey-secondary">
            No levels: asks stay with their owner however long they wait.
          </p>
        ) : null}
        {draft.levels.length < MAX_LEVELS ? (
          <Pill
            variant="ghost"
            size="sm"
            className="self-start"
            onClick={() =>
              setDraft((current) => ({
                ...current,
                levels: [...current.levels, newLevel(current.levels[current.levels.length - 1])],
              }))
            }
          >
            <Plus size={14} />
            Add a level
          </Pill>
        ) : null}
      </Card>

      {attempted && problems.length > 0 ? (
        <ul className="text-[12px] text-rag-red">
          {problems.map((problem) => (
            <li key={problem}>{problem}</li>
          ))}
        </ul>
      ) : null}
      <div className="flex flex-wrap justify-end gap-2">
        {scope !== TENANT && matrix.source === "project" ? (
          <Pill
            variant="ghost"
            size="sm"
            disabled={reset.isPending}
            onClick={() =>
              setConfirm({
                open: true,
                title: "Use the tenant's matrix?",
                description: "This project's own levels are removed.",
                confirmLabel: "Use the tenant's",
                onConfirm: () => reset.mutate(),
              })
            }
          >
            Use the tenant&apos;s matrix
          </Pill>
        ) : null}
        <Pill
          variant="ghost"
          size="sm"
          disabled={!changed}
          onClick={() => {
            setDraft(draftFromMatrix(matrix));
            setAttempted(false);
          }}
        >
          <RotateCcw size={14} />
          Undo changes
        </Pill>
        <Pill
          size="sm"
          disabled={(!changed && !inherited) || save.isPending}
          onClick={() => {
            setAttempted(true);
            if (problems.length === 0) save.mutate();
          }}
        >
          {save.isPending ? "Saving…" : inherited ? "Give it its own" : "Save escalation"}
        </Pill>
      </div>
      <ConfirmDialog
        state={confirm}
        onOpenChange={(open) => (open ? undefined : setConfirm({ open: false }))}
      />
    </div>
  );
}

function DaysInput({
  need,
  level,
  value,
  onChange,
}: {
  need: NeedType;
  level: number;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <TextInput
      aria-label={`${NEED_LABELS[need]} reaches level ${level} after days`}
      className="w-[76px] py-2 text-center text-[14px] tabular-nums"
      inputMode="numeric"
      value={value}
      placeholder="never"
      onChange={(event) => onChange(event.target.value.replace(/[^\d]/g, "").slice(0, 3))}
    />
  );
}
