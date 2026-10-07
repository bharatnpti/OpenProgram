import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  ConfigNodeResponse,
  ContactSource,
  EscalationMatrixResponse,
  NeedType,
} from "../../api/schema";
import { ConfirmDialog } from "../../components/Dialogs";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { cn } from "../../lib/utils";
import { Problems, TabIntro, hintClass, inputClass, labelClass } from "./AdminBits";
import { errorText } from "./adminWords";
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
  levelSummary,
  newLevel,
  requestFromDraft,
  sameMatrix,
} from "./escalationForm";
import { savedLine, useMemberNames, useMembers } from "./members";

const OVERVIEW_KEY = ["config", "escalation"] as const;
const TENANT = "";

/**
 * After how long each kind of ask in a day report goes up a level, and who
 * stands at each level. The tenant's matrix is what every project uses until
 * it gets its own; a project's own can be dropped to go back to the tenant's.
 */
export function EscalationTab() {
  const [scope, setScope] = useState(TENANT);
  const overview = useQuery({
    queryKey: OVERVIEW_KEY,
    queryFn: () => apiClient.escalationOverview(),
  });
  const projects = useQuery({
    queryKey: ["config", "entities", "project"],
    queryFn: () => apiClient.configProjects(),
  });
  const members = useMembers();
  const project = useQuery({
    queryKey: [...OVERVIEW_KEY, "project", scope],
    queryFn: () => apiClient.projectEscalation(scope),
    enabled: scope !== TENANT,
  });
  const own = new Set((overview.data?.projects ?? []).map((item) => item.project_id));
  const matrix = scope === TENANT ? overview.data?.tenant : project.data;

  return (
    <PanelState
      needs="an admin"
      isLoading={overview.isLoading}
      error={overview.error}
      onRetry={() => void overview.refetch()}
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        <TabIntro>
          Everything a day report lists under “What we need, and from whom” is an ask: a fix, a
          decision, an answer or a review, owned by the one person who can do it. That owner is
          level 1. The longer an ask waits, the higher it goes: each level says after how many days
          each kind of ask reaches it, and who stands there.
        </TabIntro>
        <div className="flex flex-wrap items-end gap-3 rounded-2xl bg-grey-fill px-4 py-3">
          <div className="min-w-[240px] flex-1">
            <label htmlFor="escalation-scope" className={labelClass}>
              Matrix for
            </label>
            <select
              id="escalation-scope"
              className={inputClass}
              value={scope}
              onChange={(event) => setScope(event.target.value)}
            >
              <option value={TENANT}>Every project without its own (the tenant's)</option>
              {(projects.data ?? []).map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name}
                  {own.has(item.id) ? " (its own)" : ""}
                </option>
              ))}
            </select>
          </div>
          {matrix ? (
            <RagChip tone={matrix.source === "default" ? "neutral" : "info"} className="mb-2">
              {MATRIX_SOURCE_LABELS[matrix.source]}
              {scope !== TENANT && matrix.source !== "project" ? ", inherited" : ""}
            </RagChip>
          ) : null}
        </div>
        {scope !== TENANT && project.error ? (
          <PanelState needs="an admin" isLoading={false} error={project.error}>
            {null}
          </PanelState>
        ) : !matrix ? (
          <p className="text-[13px] text-grey-secondary">Reading the matrix…</p>
        ) : (
          <MatrixEditor
            key={`${scope}:${matrix.source}:${matrix.updated_at ?? ""}`}
            scope={scope}
            projectName={(projects.data ?? []).find((item) => item.id === scope)?.name ?? scope}
            matrix={matrix}
            members={members.data ?? []}
          />
        )}
      </div>
    </PanelState>
  );
}

function MatrixEditor({
  scope,
  projectName,
  matrix,
  members,
}: {
  scope: string;
  projectName: string;
  matrix: EscalationMatrixResponse;
  members: ConfigNodeResponse[];
}) {
  const queryClient = useQueryClient();
  const nameOf = useMemberNames();
  const [draft, setDraft] = useState<MatrixDraft>(() => draftFromMatrix(matrix));
  const [attempted, setAttempted] = useState(false);
  const inherited = scope !== TENANT && matrix.source !== "project";
  const changed = !sameMatrix(draft, draftFromMatrix(matrix));
  const problems = draftProblems(draft);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: OVERVIEW_KEY });
    // Overall's matrix and the day report's asks follow it.
    void queryClient.invalidateQueries({ queryKey: ["escalation"] });
    void queryClient.invalidateQueries({ queryKey: ["day-reports"] });
  };
  const save = useMutation({
    mutationFn: () =>
      scope === TENANT
        ? apiClient.saveTenantEscalation(requestFromDraft(draft))
        : apiClient.saveProjectEscalation(scope, requestFromDraft(draft)),
    onSuccess: () => {
      toast.success(
        scope === TENANT
          ? "Saved the tenant's matrix. Every project without its own follows it."
          : `Saved ${projectName}'s own matrix.`,
      );
      refresh();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const reset = useMutation({
    mutationFn: () => apiClient.removeProjectEscalation(scope),
    onSuccess: () => {
      toast.success(`${projectName} uses the tenant's matrix again.`);
      refresh();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const update = (index: number, change: (level: LevelDraft) => LevelDraft) =>
    setDraft((current) => ({
      ...current,
      levels: current.levels.map((level, at) => (at === index ? change(level) : level)),
    }));
  const savedText = savedLine(matrix.updated_at, matrix.updated_by, nameOf);

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      <p className="text-[13px] text-grey-body">
        {inherited
          ? `${projectName} uses the ${matrix.source === "tenant" ? "tenant's" : "default"} matrix. Save a change here to give it its own.`
          : matrix.source === "default"
            ? "Nothing is saved yet: this is the default every project uses."
            : (savedText ?? "Saved.")}
      </p>

      <Panel title="Who decides">
        <label htmlFor="escalation-decider" className="sr-only">
          Who decides
        </label>
        <select
          id="escalation-decider"
          className={inputClass}
          value={draft.decisionOwnerId}
          onChange={(event) => {
            const decisionOwnerId = event.target.value;
            setDraft((current) => ({ ...current, decisionOwnerId }));
          }}
        >
          <option value="">Nobody named yet</option>
          {draft.decisionOwnerId && !members.some((m) => m.id === draft.decisionOwnerId) ? (
            <option value={draft.decisionOwnerId}>
              {draft.decisionOwnerId} (no longer a member)
            </option>
          ) : null}
          {members.map((member) => (
            <option key={member.id} value={member.id}>
              {member.name}
            </option>
          ))}
        </select>
        <p className={hintClass}>
          A decision, such as accepting a requirement, goes to this person when nothing else names
          its owner. Usually the product owner.
        </p>
      </Panel>

      <Panel title="Levels" note={`Level 1 is always the ask's owner`}>
        <p className="mb-3 text-[13px] text-grey-body">
          Days count how long an ask has waited since it first appeared. Leave a day empty and that
          kind of ask never reaches the level. A higher level is never reached sooner than the one
          below it.
        </p>
        {draft.levels.length === 0 ? (
          <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
            No levels: an ask stays with its owner however long it waits.
          </p>
        ) : (
          <TableBox>
            <table className="w-full min-w-[760px] border-collapse">
              <thead>
                <tr>
                  <th className={th}>Level</th>
                  <th className={th}>Goes to</th>
                  {NEEDS.map((need) => (
                    <th key={need} className={th} title={NEED_HINTS[need]}>
                      {NEED_LABELS[need]}
                      <span className="block text-[10px] font-normal normal-case tracking-normal">
                        after days
                      </span>
                    </th>
                  ))}
                  <th className={th} />
                </tr>
              </thead>
              <tbody>
                {draft.levels.map((level, index) => (
                  <tr key={index}>
                    <td className={td}>
                      <span className="mb-1 block text-[11px] font-bold text-grey-secondary">
                        {index + 2}
                      </span>
                      <input
                        aria-label={`Level ${index + 2} name`}
                        className={inputClass}
                        value={level.label}
                        maxLength={60}
                        placeholder="Delivery lead"
                        onChange={(event) => {
                          const label = event.target.value;
                          update(index, (current) => ({ ...current, label }));
                        }}
                      />
                    </td>
                    <td className={td}>
                      <span className="mb-1 block text-[11px]">&nbsp;</span>
                      <select
                        aria-label={`Level ${index + 2} goes to`}
                        className={inputClass}
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
                      </select>
                      {level.source === "member" ? (
                        <select
                          aria-label={`Member level ${index + 2} goes to`}
                          className={cn(inputClass, "mt-2")}
                          value={level.memberId}
                          onChange={(event) => {
                            const memberId = event.target.value;
                            update(index, (current) => ({ ...current, memberId }));
                          }}
                        >
                          <option value="">Pick a member</option>
                          {level.memberId && !members.some((m) => m.id === level.memberId) ? (
                            <option value={level.memberId}>
                              {level.memberId} (no longer a member)
                            </option>
                          ) : null}
                          {members.map((member) => (
                            <option key={member.id} value={member.id}>
                              {member.name}
                            </option>
                          ))}
                        </select>
                      ) : null}
                    </td>
                    {NEEDS.map((need) => (
                      <td key={need} className={td}>
                        <span className="mb-1 block text-[11px]">&nbsp;</span>
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
                    <td className={td}>
                      <span className="mb-1 block text-[11px]">&nbsp;</span>
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
                        <X size={16} aria-hidden />
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableBox>
        )}
        {draft.levels.length > 0 ? (
          <ul className="mt-3 grid gap-1 text-[13px] text-grey-body">
            {requestFromDraft(draft).levels.map((level, index) => (
              <li key={index}>
                <b>
                  Level {index + 2}
                  {level.label ? `, ${level.label}` : ""}
                </b>{" "}
                (
                {level.source === "member"
                  ? nameOf(level.member_id)
                  : SOURCE_LABELS[level.source].toLowerCase()}
                ): {levelSummary(level)}
              </li>
            ))}
          </ul>
        ) : null}
        {draft.levels.length < MAX_LEVELS ? (
          <Pill
            variant="ghost"
            size="sm"
            className="mt-3"
            onClick={() =>
              setDraft((current) => ({
                ...current,
                levels: [...current.levels, newLevel(current.levels[current.levels.length - 1])],
              }))
            }
          >
            <Plus size={14} aria-hidden />
            Add a level
          </Pill>
        ) : null}
      </Panel>

      {attempted ? <Problems problems={problems} /> : null}
      <div className="flex flex-wrap justify-end gap-2">
        {scope !== TENANT && matrix.source === "project" ? (
          <ConfirmDialog
            trigger={
              <Pill variant="ghost" size="sm" disabled={reset.isPending}>
                Use the tenant's matrix
              </Pill>
            }
            title={`Use the tenant's matrix for ${projectName}?`}
            description="This project's own levels and decision owner are removed; it follows the tenant's matrix from now on."
            confirmLabel="Use the tenant's"
            onConfirm={() => reset.mutate()}
          />
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
    <input
      aria-label={`${NEED_LABELS[need]}: days before it reaches level ${level}`}
      className={cn(inputClass, "w-[72px] text-center tabular-nums")}
      inputMode="numeric"
      value={value}
      placeholder="never"
      onChange={(event) => onChange(event.target.value.replace(/[^\d]/g, "").slice(0, 3))}
    />
  );
}
