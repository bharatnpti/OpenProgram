import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  DeliveryStage,
  ReadinessAppliesTo,
  ReadinessConfigResponse,
  ReadinessCriterionDto,
  ReadinessMatcherKind,
  ReadinessPreviewResponse,
  ReadinessSettingsDto,
  ReadinessStrength,
} from "../../api/schema";
import { useProjects } from "../../app/directory";
import { ConfirmDialog } from "../../components/Dialogs";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { STAGE_LABELS } from "../../components/viz/stages";
import { AdminDialog, Problems, TabIntro, hintClass, inputClass, labelClass } from "./AdminBits";
import { errorText } from "./adminWords";
import {
  APPLIES_LABELS,
  DEFAULT_STRENGTH,
  MATCHER_LABELS,
  MAX_MATCHERS,
  PLACEHOLDERS,
  type CriterionDraft,
  type MatcherDraft,
  appliesLine,
  criterionFromDraft,
  draftFromCriterion,
  draftProblems,
  foundByLine,
  list,
  newCriterionDraft,
  newMatcher,
  settingsProblems,
} from "./readinessForm";
import { STEPS } from "./stageMapping";

const CONFIG_KEY = ["config", "readiness"] as const;

/**
 * Release readiness: what a release, project or pod needs before it moves on,
 * beyond the gates each requirement passes. The agent looks for it in Jira every
 * hour and drafts the issues that are missing; a person creates them. None is on
 * until an admin adds one, from a blank form or an example.
 */
export function ReleaseReadinessTab() {
  const queryClient = useQueryClient();
  const config = useQuery({ queryKey: CONFIG_KEY, queryFn: () => apiClient.readinessConfig() });
  const [editing, setEditing] = useState<CriterionDraft | null>(null);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: CONFIG_KEY });
    // Overall's section and the day report read these.
    void queryClient.invalidateQueries({ queryKey: ["readiness"] });
    void queryClient.invalidateQueries({ queryKey: ["day-reports"] });
  };
  const remove = useMutation({
    mutationFn: (criterionId: string) => apiClient.removeReadinessCriterion(criterionId),
    onSuccess: () => {
      toast.success("Criterion removed. It is no longer checked; what people decided is kept.");
      refresh();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const data = config.data;

  return (
    <PanelState
      isLoading={config.isLoading}
      error={config.error}
      onRetry={() => void config.refetch()}
    >
      {data ? (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
          <TabIntro>
            What a release, project or pod needs before it moves on, beyond the gates each
            requirement passes: a security review, a load test, a runbook. The agent looks for it in
            Jira every hour and drafts the issues that are missing. Nothing is created in Jira until
            a person presses Create on one draft.
          </TabIntro>
          <AgentCard config={data} onSaved={refresh} />
          <section className="grid gap-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <h3 className="text-[17px] font-extrabold">Criteria</h3>
              <div className="flex flex-wrap items-center gap-2">
                <ExamplePicker
                  examples={data.examples}
                  onPick={(example) => setEditing(draftFromCriterion(example))}
                />
                <Pill size="sm" onClick={() => setEditing(newCriterionDraft())}>
                  <Plus size={14} aria-hidden />
                  Add a criterion
                </Pill>
              </div>
            </div>
            {data.criteria.length === 0 ? (
              <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
                No criteria yet. Add one from an example, or start from a blank one.
              </p>
            ) : (
              <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-2">
                {data.criteria.map((criterion) => (
                  <CriterionCard
                    key={criterion.criterion_id}
                    criterion={criterion}
                    onEdit={() => setEditing(draftFromCriterion(criterion))}
                    onRemove={() => remove.mutate(criterion.criterion_id ?? "")}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      ) : null}
      {editing ? (
        <CriterionDialog
          draft={editing}
          isExample={
            !data?.criteria.some((item) => item.criterion_id === editing.criterionId) &&
            editing.criterionId !== ""
          }
          onChange={(update) => setEditing((current) => (current ? update(current) : current))}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      ) : null}
    </PanelState>
  );
}

function AgentCard({ config, onSaved }: { config: ReadinessConfigResponse; onSaved: () => void }) {
  const [settings, setSettings] = useState<ReadinessSettingsDto>(config.settings);
  const [labelsText, setLabelsText] = useState((config.settings.labels ?? []).join(", "));
  const [attempted, setAttempted] = useState(false);
  const next = { ...settings, labels: list(labelsText) };
  const problems = settingsProblems(next);
  const save = useMutation({
    mutationFn: () => apiClient.saveReadinessSettings(next),
    onSuccess: (saved) => {
      toast.success(
        saved.enabled ? "Saved. The agent checks every hour." : "Saved. The agent is off.",
      );
      onSaved();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const toggle = (key: "enabled" | "auto_suggest" | "create_in_jira") => (on: boolean) =>
    setSettings((current) => ({ ...current, [key]: on }));

  return (
    <section className="grid gap-3 rounded-3xl border border-grey-border bg-white p-5">
      <h3 className="text-[17px] font-extrabold">The agent</h3>
      <Switch
        id="rr-enabled"
        label="Check"
        on={settings.enabled}
        onChange={toggle("enabled")}
        hint="Every hour after the Jira sync. Anyone in scope can also run it now from Overall."
      />
      <Switch
        id="rr-drafts"
        label="Draft issues"
        on={settings.auto_suggest}
        onChange={toggle("auto_suggest")}
        hint="A draft Jira issue for each missing criterion. Off, a person drafts one when they want it."
      />
      <Switch
        id="rr-create"
        label="Create in Jira"
        on={settings.create_in_jira}
        onChange={toggle("create_in_jira")}
        hint={
          config.writeback_enabled
            ? 'Lets a person press "Create in Jira" on a draft. Jira write-back is on for this tenant.'
            : 'Lets a person press "Create in Jira" on a draft. Jira write-back is off for this tenant, so creating stays off until it is on.'
        }
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="rr-type" className={labelClass}>
            New issues: type
          </label>
          <input
            id="rr-type"
            className={inputClass}
            value={settings.issue_type}
            maxLength={60}
            onChange={(event) => {
              const issue_type = event.target.value;
              setSettings((current) => ({ ...current, issue_type }));
            }}
          />
        </div>
        <div>
          <label htmlFor="rr-labels" className={labelClass}>
            New issues: labels
          </label>
          <input
            id="rr-labels"
            className={inputClass}
            value={labelsText}
            onChange={(event) => setLabelsText(event.target.value)}
          />
          <p className={hintClass}>Separated by commas. Created issues are never assigned.</p>
        </div>
      </div>
      <p className="text-[12px] text-grey-secondary">
        Only a manager or an admin marks a blocking criterion not applicable.
      </p>
      {attempted ? <Problems problems={problems} /> : null}
      <div className="flex justify-end">
        <Pill
          size="sm"
          disabled={save.isPending}
          onClick={() => {
            setAttempted(true);
            if (problems.length === 0) save.mutate();
          }}
        >
          {save.isPending ? "Saving…" : "Save the agent's settings"}
        </Pill>
      </div>
    </section>
  );
}

function Switch({
  id,
  label,
  on,
  onChange,
  hint,
}: {
  id: string;
  label: string;
  on: boolean;
  onChange: (on: boolean) => void;
  hint: string;
}) {
  return (
    <div className="flex items-start gap-3">
      <input
        id={id}
        type="checkbox"
        role="switch"
        aria-checked={on}
        checked={on}
        className="mt-1 h-4 w-4"
        onChange={(event) => onChange(event.target.checked)}
      />
      <label htmlFor={id} className="min-w-0">
        <span className="block text-[14px] font-bold">
          {label}: {on ? "on" : "off"}
        </span>
        <span className="block text-[12.5px] text-grey-body">{hint}</span>
      </label>
    </div>
  );
}

function ExamplePicker({
  examples,
  onPick,
}: {
  examples: ReadinessCriterionDto[];
  onPick: (example: ReadinessCriterionDto) => void;
}) {
  if (examples.length === 0) return null;
  return (
    <label className="flex items-center gap-2 text-[13px] font-bold">
      <span className="sr-only">Add from an example</span>
      <select
        className="h-9 rounded-full border border-ink bg-white px-3 text-[13px] font-bold"
        value=""
        onChange={(event) => {
          const example = examples.find((item) => item.criterion_id === event.target.value);
          if (example) onPick(example);
        }}
      >
        <option value="">From an example…</option>
        {examples.map((example) => (
          <option key={example.criterion_id} value={example.criterion_id ?? ""}>
            {example.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function CriterionCard({
  criterion,
  onEdit,
  onRemove,
}: {
  criterion: ReadinessCriterionDto;
  onEdit: () => void;
  onRemove: () => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-2 rounded-3xl border border-grey-border bg-white p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-[16px] font-extrabold">{criterion.name}</h4>
            {criterion.enabled === false ? (
              <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                off
              </RagChip>
            ) : null}
          </div>
          <p className="mt-0.5 text-[13px] text-grey-body">
            Before {STAGE_LABELS[criterion.required_before ?? "production"]} ·{" "}
            {appliesLine(criterion)}
          </p>
        </div>
        <div className="flex flex-none gap-1">
          <button
            type="button"
            aria-label={`Change ${criterion.name}`}
            className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
            onClick={onEdit}
          >
            <Pencil size={15} aria-hidden />
          </button>
          <ConfirmDialog
            trigger={
              <button
                type="button"
                aria-label={`Remove ${criterion.name}`}
                className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
              >
                <Trash2 size={15} aria-hidden />
              </button>
            }
            title={`Remove ${criterion.name}?`}
            description="It is no longer checked or shown. What people linked, waived or dismissed for it is kept, and it comes back if it is added again."
            confirmLabel="Remove criterion"
            onConfirm={onRemove}
          />
        </div>
      </div>
      <p className="text-[13px] text-grey-body">{criterion.evidence}</p>
      <p className="text-[12px] text-grey-secondary">{foundByLine(criterion)}</p>
    </section>
  );
}

function CriterionDialog({
  draft,
  isExample,
  onChange,
  onClose,
  onSaved,
}: {
  draft: CriterionDraft;
  isExample: boolean;
  /** Changes apply to the latest draft, so quick edits never undo each other. */
  onChange: (update: (draft: CriterionDraft) => CriterionDraft) => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [attempted, setAttempted] = useState(false);
  const problems = draftProblems(draft);
  const save = useMutation({
    mutationFn: () => apiClient.saveReadinessCriterion(criterionFromDraft(draft)),
    onSuccess: (saved) => {
      toast.success(`${saved.name} saved. The next check reads it.`);
      onSaved();
      onClose();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const set = <K extends keyof CriterionDraft>(key: K, value: CriterionDraft[K]) =>
    onChange((current) => ({ ...current, [key]: value }));
  const setMatcher = (index: number, update: (matcher: MatcherDraft) => MatcherDraft) =>
    onChange((current) => ({
      ...current,
      matchers: current.matchers.map((item, at) => (at === index ? update(item) : item)),
    }));
  const isNew = draft.criterionId === "" || isExample;

  return (
    <AdminDialog
      open
      wide
      onOpenChange={(open) => (open ? undefined : onClose())}
      title={isNew ? (isExample ? `Add ${draft.name}` : "Add a criterion") : `Change ${draft.name}`}
      description="A saved criterion is checked from the next run; Run check now on Overall reads it at once."
    >
      <form
        className="mt-4 grid gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setAttempted(true);
          if (problems.length === 0) save.mutate();
        }}
      >
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
          <Field id="rc-name" label="Name">
            <input
              id="rc-name"
              className={inputClass}
              value={draft.name}
              maxLength={80}
              placeholder="Security review"
              onChange={(event) => set("name", event.target.value)}
            />
          </Field>
          <Field id="rc-applies" label="Applies to">
            <select
              id="rc-applies"
              className={inputClass}
              value={draft.appliesTo}
              onChange={(event) => set("appliesTo", event.target.value as ReadinessAppliesTo)}
            >
              {(Object.keys(APPLIES_LABELS) as ReadinessAppliesTo[]).map((key) => (
                <option key={key} value={key}>
                  {APPLIES_LABELS[key]}
                </option>
              ))}
            </select>
          </Field>
        </div>
        <Field id="rc-evidence" label="What counts">
          <textarea
            id="rc-evidence"
            className={`${inputClass} h-20 py-2`}
            value={draft.evidence}
            maxLength={600}
            placeholder="A security review of the release's changes, with its findings closed or accepted."
            onChange={(event) => set("evidence", event.target.value)}
          />
        </Field>
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-3">
          <Field id="rc-stage" label="Needed before">
            <select
              id="rc-stage"
              className={inputClass}
              value={draft.requiredBefore}
              onChange={(event) => set("requiredBefore", event.target.value as DeliveryStage)}
            >
              {STEPS.map((step) => (
                <option key={step} value={step}>
                  {STAGE_LABELS[step]}
                </option>
              ))}
            </select>
          </Field>
          <Field id="rc-lead" label="Working days before the date">
            <input
              id="rc-lead"
              className={inputClass}
              inputMode="numeric"
              value={draft.leadText}
              onChange={(event) => set("leadText", event.target.value)}
            />
          </Field>
          <Field id="rc-severity" label="Severity">
            <select
              id="rc-severity"
              className={inputClass}
              value={draft.blocking ? "blocking" : "advisory"}
              onChange={(event) => set("blocking", event.target.value === "blocking")}
            >
              <option value="blocking">Blocking: the day report asks for it</option>
              <option value="advisory">Advisory: shown on Overall only</option>
            </select>
          </Field>
        </div>
        <div className="flex flex-wrap gap-x-5 gap-y-2">
          <label className="flex items-center gap-1.5 text-[14px]">
            <input
              type="checkbox"
              checked={draft.needsDone}
              onChange={(event) => set("needsDone", event.target.checked)}
            />
            Ready only when an evidence issue is done
          </label>
          <label className="flex items-center gap-1.5 text-[14px] font-bold">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => set("enabled", event.target.checked)}
            />
            On: check scopes against it
          </label>
        </div>
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
          <Field id="rc-when-labels" label="Only where an issue has the label (optional)">
            <input
              id="rc-when-labels"
              className={inputClass}
              value={draft.whenLabelsText}
              placeholder="personal-data"
              onChange={(event) => set("whenLabelsText", event.target.value)}
            />
          </Field>
          <Field id="rc-when-types" label="…or the issue type (optional)">
            <input
              id="rc-when-types"
              className={inputClass}
              value={draft.whenTypesText}
              onChange={(event) => set("whenTypesText", event.target.value)}
            />
          </Field>
        </div>

        <fieldset className="grid gap-2">
          <legend className="mb-1 text-[15px] font-extrabold">Found by</legend>
          <p className={hintClass}>
            A match that counts covers the criterion. A hint only makes it unsure, and a person
            decides.
          </p>
          {draft.matchers.map((matcher, index) => (
            <MatcherRow
              key={index}
              index={index}
              matcher={matcher}
              onChange={(update) => setMatcher(index, update)}
              onRemove={
                draft.matchers.length > 1
                  ? () =>
                      onChange((current) => ({
                        ...current,
                        matchers: current.matchers.filter((_, at) => at !== index),
                      }))
                  : undefined
              }
            />
          ))}
          {draft.matchers.length < MAX_MATCHERS ? (
            <Pill
              type="button"
              variant="ghost"
              size="sm"
              className="justify-self-start"
              onClick={() =>
                onChange((current) => ({
                  ...current,
                  matchers: [...current.matchers, newMatcher()],
                }))
              }
            >
              <Plus size={14} aria-hidden />
              Add a way
            </Pill>
          ) : null}
        </fieldset>

        <fieldset className="grid gap-3">
          <legend className="mb-1 text-[15px] font-extrabold">The drafted issue</legend>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-3">
            <Field id="rc-project" label="Jira project">
              <input
                id="rc-project"
                className={inputClass}
                value={draft.projectKey}
                placeholder="The scope's own"
                onChange={(event) => set("projectKey", event.target.value)}
              />
            </Field>
            <Field id="rc-type" label="Issue type">
              <input
                id="rc-type"
                className={inputClass}
                value={draft.issueType}
                placeholder="The tenant's"
                onChange={(event) => set("issueType", event.target.value)}
              />
            </Field>
            <Field id="rc-labels" label="Labels">
              <input
                id="rc-labels"
                className={inputClass}
                value={draft.labelsText}
                placeholder="security-review"
                onChange={(event) => set("labelsText", event.target.value)}
              />
            </Field>
          </div>
          <Field id="rc-summary" label="Summary">
            <input
              id="rc-summary"
              className={inputClass}
              value={draft.summary}
              maxLength={255}
              onChange={(event) => set("summary", event.target.value)}
            />
          </Field>
          <Field id="rc-text" label="Text (optional)">
            <textarea
              id="rc-text"
              className={`${inputClass} h-24 py-2`}
              value={draft.description}
              maxLength={4000}
              placeholder="Empty: OpenProgram writes what the scope needs, what counts and by when."
              onChange={(event) => set("description", event.target.value)}
            />
          </Field>
          <p className={hintClass}>
            Words it fills in: {PLACEHOLDERS.map((name) => `{${name}}`).join(" ")}. A draft never
            copies Jira text, and is never assigned to anyone.
          </p>
        </fieldset>

        <TryOnProject criterion={criterionFromDraft(draft)} disabled={problems.length > 0} />
        {attempted ? <Problems problems={problems} /> : null}
        <div className="flex justify-end gap-2">
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save criterion"}
          </Pill>
        </div>
      </form>
    </AdminDialog>
  );
}

function MatcherRow({
  index,
  matcher,
  onChange,
  onRemove,
}: {
  index: number;
  matcher: MatcherDraft;
  onChange: (update: (matcher: MatcherDraft) => MatcherDraft) => void;
  onRemove?: () => void;
}) {
  const id = `rc-matcher-${index}`;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] items-end gap-2 rounded-2xl border border-grey-border p-3 sm:grid-cols-[150px_minmax(0,1fr)_150px_auto]">
      <div>
        <label htmlFor={`${id}-kind`} className={labelClass}>
          Way {index + 1}
        </label>
        <select
          id={`${id}-kind`}
          className={inputClass}
          value={matcher.kind}
          onChange={(event) => {
            const kind = event.target.value as ReadinessMatcherKind;
            onChange((current) => ({ ...current, kind, strength: DEFAULT_STRENGTH[kind] }));
          }}
        >
          {(Object.keys(MATCHER_LABELS) as ReadinessMatcherKind[]).map((kind) => (
            <option key={kind} value={kind}>
              {MATCHER_LABELS[kind]}
            </option>
          ))}
        </select>
      </div>
      <div className="min-w-0">
        <label htmlFor={`${id}-value`} className={labelClass}>
          Value
        </label>
        <input
          id={`${id}-value`}
          className={inputClass}
          value={matcher.value}
          maxLength={80}
          onChange={(event) => {
            const value = event.target.value;
            onChange((current) => ({ ...current, value }));
          }}
        />
      </div>
      <div>
        <label htmlFor={`${id}-strength`} className={labelClass}>
          A match
        </label>
        <select
          id={`${id}-strength`}
          className={inputClass}
          value={matcher.strength}
          onChange={(event) => {
            const strength = event.target.value as ReadinessStrength;
            onChange((current) => ({ ...current, strength }));
          }}
        >
          <option value="evidence">counts</option>
          <option value="candidate">is only a hint</option>
        </select>
      </div>
      {onRemove ? (
        <button
          type="button"
          aria-label={`Remove way ${index + 1}`}
          className="mb-1 justify-self-start rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
          onClick={onRemove}
        >
          <X size={16} aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

/** What the agent would find on one project with the criterion as it stands. Saves nothing. */
function TryOnProject({
  criterion,
  disabled,
}: {
  criterion: ReadinessCriterionDto;
  disabled: boolean;
}) {
  const projects = useProjects();
  const [projectId, setProjectId] = useState("");
  const [result, setResult] = useState<ReadinessPreviewResponse | null>(null);
  const preview = useMutation({
    mutationFn: (id: string) => apiClient.previewReadinessCriterion(id, criterion),
    onSuccess: setResult,
    onError: (error) => toast.error(errorText(error)),
  });
  const chosen = projectId || projects.data?.[0]?.id || "";
  return (
    <div className="grid gap-2 rounded-2xl bg-grey-fill px-4 py-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-[1_1_200px]">
          <label htmlFor="rc-try" className={labelClass}>
            Try on a project
          </label>
          <select
            id="rc-try"
            className={inputClass}
            value={chosen}
            onChange={(event) => setProjectId(event.target.value)}
          >
            {(projects.data ?? []).map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>
        </div>
        <Pill
          type="button"
          size="sm"
          variant="ghost"
          disabled={disabled || !chosen || preview.isPending}
          onClick={() => preview.mutate(chosen)}
        >
          {preview.isPending ? "Looking…" : "Show what it finds"}
        </Pill>
      </div>
      {result ? (
        result.rows.length === 0 ? (
          <p className="text-[13px] text-grey-body">
            It does not apply to any scope of this project.
          </p>
        ) : (
          <ul className="grid gap-1 text-[13px]">
            {result.rows.map((row) => (
              <li key={`${row.scope.kind}-${row.scope.id}`}>
                <b>{row.scope.name}</b>:{" "}
                {!row.applies
                  ? "does not apply here"
                  : row.state === "covered"
                    ? `covered by ${row.evidence.map((item) => item.issue_key || "a record").join(", ")}`
                    : row.state === "unsure"
                      ? `unsure: ${row.candidates.map((item) => item.issue_key).join(", ")}`
                      : "missing"}
              </li>
            ))}
          </ul>
        )
      ) : null}
    </div>
  );
}

function Field({ id, label, children }: { id: string; label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <label htmlFor={id} className={labelClass}>
        {label}
      </label>
      {children}
    </div>
  );
}
