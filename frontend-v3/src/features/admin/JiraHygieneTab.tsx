import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2, X } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  DeliveryStage,
  HygieneConditionKind,
  HygieneConfigResponse,
  HygieneIssueField,
  HygieneOwnerKind,
  HygienePreviewResponse,
  HygieneRuleDto,
  HygieneRuleNeed,
  HygieneRuleScope,
  HygieneRuleSeverity,
  HygieneStageFilter,
  ReadinessMatcherKind,
} from "../../api/schema";
import { useProjects } from "../../app/directory";
import { ConfirmDialog } from "../../components/Dialogs";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { STAGE_LABELS } from "../../components/viz/stages";
import { profileOptions } from "../profiles/profiles";
import { useProfileCatalog } from "../profiles/useProfiles";
import { AdminDialog, Problems, TabIntro, hintClass, inputClass, labelClass } from "./AdminBits";
import { errorText } from "./adminWords";
import {
  CONDITION_LABELS,
  FIELD_LABELS,
  FIX_PLACEHOLDERS,
  MAX_SELECT,
  OWNER_LABELS,
  SCOPE_LABELS,
  SELECT_LABELS,
  STAGE_FILTER_LABELS,
  type RuleDraft,
  type SelectDraft,
  type SettingsDraft,
  draftFromRule,
  draftProblems,
  modeLine,
  ownerLine,
  newRuleDraft,
  previewLine,
  ruleFromDraft,
  settingsBody,
  settingsDraft,
  settingsProblems,
} from "./hygieneForm";
import { STEPS } from "./stageMapping";

const CONFIG_KEY = ["config", "hygiene"] as const;

/**
 * Jira hygiene: per-issue rules the tenant keeps, checked against the synced Jira
 * issues every hour: an addition after planning without a change label, a story
 * with no estimate, a bug with no severity. None is on until an admin adds one,
 * and each lists only until it is switched to ask its owner. Try on a project
 * shows what a rule would find before it reaches anyone. Nothing is written to Jira.
 */
export function JiraHygieneTab() {
  const queryClient = useQueryClient();
  const config = useQuery({ queryKey: CONFIG_KEY, queryFn: () => apiClient.hygieneConfig() });
  const [editing, setEditing] = useState<RuleDraft | null>(null);
  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: CONFIG_KEY });
    // Overall's section and the day report read these.
    void queryClient.invalidateQueries({ queryKey: ["hygiene"] });
    void queryClient.invalidateQueries({ queryKey: ["day-reports"] });
  };
  const remove = useMutation({
    mutationFn: (ruleId: string) => apiClient.removeHygieneRule(ruleId),
    onSuccess: () => {
      toast.success("Rule removed. It is no longer checked; what people decided is kept.");
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
            Rules for what each Jira issue should have, checked against the synced issues every
            hour: an addition after planning without a change label, a story without an estimate,
            work with no movement. Each finding names the issue, who is asked and a suggested fix. A
            rule lists only until you switch it to ask its owner. Nothing is written to Jira.
          </TabIntro>
          <ScanCard key={data.settings.updated_at ?? "default"} config={data} onSaved={refresh} />
          <section className="grid gap-3">
            <div className="flex flex-wrap items-end justify-between gap-3">
              <h3 className="text-[17px] font-extrabold">Rules</h3>
              <div className="flex flex-wrap items-center gap-2">
                <ExamplePicker
                  examples={data.examples}
                  onPick={(example) => setEditing(draftFromRule(example))}
                />
                <Pill size="sm" onClick={() => setEditing(newRuleDraft())}>
                  <Plus size={14} aria-hidden />
                  Add a rule
                </Pill>
              </div>
            </div>
            {data.rules.length === 0 ? (
              <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
                No rules yet. Add one from an example, or start from a blank one.
              </p>
            ) : (
              <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-2">
                {data.rules.map((rule) => (
                  <RuleCard
                    key={rule.rule_id}
                    rule={rule}
                    onEdit={() => setEditing(draftFromRule(rule))}
                    onRemove={() => remove.mutate(rule.rule_id ?? "")}
                  />
                ))}
              </div>
            )}
          </section>
        </div>
      ) : null}
      {editing && data ? (
        <RuleDialog
          draft={editing}
          config={data}
          isExample={
            !data.rules.some((item) => item.rule_id === editing.ruleId) && editing.ruleId !== ""
          }
          onChange={(update) => setEditing((current) => (current ? update(current) : current))}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      ) : null}
    </PanelState>
  );
}

function ScanCard({ config, onSaved }: { config: HygieneConfigResponse; onSaved: () => void }) {
  const [draft, setDraft] = useState<SettingsDraft>(settingsDraft(config.settings));
  const [attempted, setAttempted] = useState(false);
  const problems = settingsProblems(draft);
  const save = useMutation({
    mutationFn: () => apiClient.saveHygieneSettings(settingsBody(draft)),
    onSuccess: (saved) => {
      toast.success(
        saved.enabled ? "Saved. The scan checks every hour." : "Saved. The scan is off.",
      );
      onSaved();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  return (
    <section className="grid gap-3 rounded-3xl border border-grey-border bg-white p-5">
      <h3 className="text-[17px] font-extrabold">The scan</h3>
      <div className="flex items-start gap-3">
        <input
          id="hy-enabled"
          type="checkbox"
          role="switch"
          aria-checked={draft.enabled}
          checked={draft.enabled}
          className="mt-1 h-4 w-4"
          onChange={(event) => {
            const enabled = event.target.checked;
            setDraft((current) => ({ ...current, enabled }));
          }}
        />
        <label htmlFor="hy-enabled" className="min-w-0">
          <span className="block text-[14px] font-bold">Check: {draft.enabled ? "on" : "off"}</span>
          <span className="block text-[12.5px] text-grey-body">
            Every hour after the Jira sync, beside release readiness. Overall also has Check now.
          </span>
        </label>
      </div>
      <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="hy-labels" className={labelClass}>
            Change labels
          </label>
          <input
            id="hy-labels"
            className={inputClass}
            value={draft.labelsText}
            placeholder="CR"
            onChange={(event) => {
              const labelsText = event.target.value;
              setDraft((current) => ({ ...current, labelsText }));
            }}
          />
          <p className={hintClass}>
            Your Jira labels for scope agreed after planning, separated by commas. A planning
            baseline's own label counts too.
          </p>
        </div>
        <div>
          <label htmlFor="hy-stale" className={labelClass}>
            Working days without a change
          </label>
          <input
            id="hy-stale"
            className={inputClass}
            inputMode="numeric"
            value={draft.daysText}
            onChange={(event) => {
              const daysText = event.target.value;
              setDraft((current) => ({ ...current, daysText }));
            }}
          />
          <p className={hintClass}>What the &ldquo;no movement&rdquo; example starts from.</p>
        </div>
      </div>
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
          {save.isPending ? "Saving…" : "Save the scan's settings"}
        </Pill>
      </div>
    </section>
  );
}

function ExamplePicker({
  examples,
  onPick,
}: {
  examples: HygieneRuleDto[];
  onPick: (example: HygieneRuleDto) => void;
}) {
  if (examples.length === 0) return null;
  return (
    <label className="flex items-center gap-2 text-[13px] font-bold">
      <span className="sr-only">Add from an example</span>
      <select
        className="h-9 max-w-[260px] rounded-full border border-ink bg-white px-3 text-[13px] font-bold"
        value=""
        onChange={(event) => {
          const example = examples.find((item) => item.rule_id === event.target.value);
          if (example) onPick(example);
        }}
      >
        <option value="">From an example…</option>
        {examples.map((example) => (
          <option key={example.rule_id} value={example.rule_id ?? ""}>
            {example.name}
          </option>
        ))}
      </select>
    </label>
  );
}

function RuleCard({
  rule,
  onEdit,
  onRemove,
}: {
  rule: HygieneRuleDto;
  onEdit: () => void;
  onRemove: () => void;
}) {
  const catalog = useProfileCatalog();
  const labels = Object.fromEntries((catalog.data ?? []).map((entry) => [entry.slug, entry.label]));
  return (
    <section className="grid min-w-0 content-start gap-2 rounded-3xl border border-grey-border bg-white p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h4 className="text-[16px] font-extrabold">{rule.name}</h4>
            <RagChip
              tone={rule.severity === "ask" ? "warning" : "neutral"}
              className="h-6 px-2.5 text-[12px]"
            >
              {modeLine(rule)}
            </RagChip>
            {rule.enabled === false ? (
              <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                off
              </RagChip>
            ) : null}
          </div>
          <p className="mt-0.5 text-[13px] text-grey-body">
            {CONDITION_LABELS[rule.condition.kind]}
            {rule.condition.field ? `: ${FIELD_LABELS[rule.condition.field]}` : ""} ·{" "}
            {SCOPE_LABELS[rule.scope ?? "project"]}
          </p>
        </div>
        <div className="flex flex-none gap-1">
          <button
            type="button"
            aria-label={`Change ${rule.name}`}
            className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
            onClick={onEdit}
          >
            <Pencil size={15} aria-hidden />
          </button>
          <ConfirmDialog
            trigger={
              <button
                type="button"
                aria-label={`Remove ${rule.name}`}
                className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
              >
                <Trash2 size={15} aria-hidden />
              </button>
            }
            title={`Remove ${rule.name}?`}
            description="It is no longer checked or shown. What people waived or set aside for it is kept, and it comes back if it is added again."
            confirmLabel="Remove rule"
            onConfirm={onRemove}
          />
        </div>
      </div>
      {rule.suggested_fix ? (
        <p className="text-[12.5px] text-grey-secondary">Suggested fix: {rule.suggested_fix}</p>
      ) : null}
      <p className="text-[12px] text-grey-secondary">{ownerLine(rule.owner, labels)}</p>
    </section>
  );
}

function RuleDialog({
  draft,
  config,
  isExample,
  onChange,
  onClose,
  onSaved,
}: {
  draft: RuleDraft;
  config: HygieneConfigResponse;
  isExample: boolean;
  /** Changes apply to the latest draft, so quick edits never undo each other. */
  onChange: (update: (draft: RuleDraft) => RuleDraft) => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [attempted, setAttempted] = useState(false);
  const catalog = useProfileCatalog();
  const problems = draftProblems(draft);
  const save = useMutation({
    mutationFn: () => apiClient.saveHygieneRule(ruleFromDraft(draft)),
    onSuccess: (saved) => {
      toast.success(`${saved.name} saved. The next check reads it.`);
      onSaved();
      onClose();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const set = <K extends keyof RuleDraft>(key: K, value: RuleDraft[K]) =>
    onChange((current) => ({ ...current, [key]: value }));
  const setMatcher = (index: number, update: (matcher: SelectDraft) => SelectDraft) =>
    onChange((current) => ({
      ...current,
      matchers: current.matchers.map((item, at) => (at === index ? update(item) : item)),
    }));
  const isNew = draft.ruleId === "" || isExample;
  const kind = draft.condition;
  const gateKinds = config.gate_kinds.length
    ? config.gate_kinds
    : [{ key: "acceptance", label: "Acceptance criteria" }];

  return (
    <AdminDialog
      open
      wide
      onOpenChange={(open) => (open ? undefined : onClose())}
      title={isNew ? (isExample ? `Add ${draft.name}` : "Add a rule") : `Change ${draft.name}`}
      description="A saved rule is checked from the next run; Check now on Overall reads it at once."
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
          <Field id="hy-name" label="Name">
            <input
              id="hy-name"
              className={inputClass}
              value={draft.name}
              maxLength={80}
              placeholder="Story without an estimate"
              onChange={(event) => set("name", event.target.value)}
            />
          </Field>
          <Field id="hy-scope" label="Reads">
            <select
              id="hy-scope"
              className={inputClass}
              value={draft.scope}
              onChange={(event) => set("scope", event.target.value as HygieneRuleScope)}
            >
              {(Object.keys(SCOPE_LABELS) as HygieneRuleScope[]).map((key) => (
                <option key={key} value={key}>
                  {SCOPE_LABELS[key]}
                </option>
              ))}
            </select>
          </Field>
        </div>

        <fieldset className="grid gap-3">
          <legend className="mb-1 text-[15px] font-extrabold">Which issues</legend>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-3">
            <Field id="hy-types" label="Types">
              <select
                id="hy-types"
                className={inputClass}
                value={draft.everyType ? "every" : "requirements"}
                onChange={(event) => set("everyType", event.target.value === "every")}
              >
                <option value="requirements">Counted requirements</option>
                <option value="every">Every issue type</option>
              </select>
            </Field>
            <Field id="hy-stage-filter" label="Where">
              <select
                id="hy-stage-filter"
                className={inputClass}
                value={draft.stageFilter}
                onChange={(event) => set("stageFilter", event.target.value as HygieneStageFilter)}
              >
                {(Object.keys(STAGE_FILTER_LABELS) as HygieneStageFilter[]).map((key) => (
                  <option key={key} value={key}>
                    {STAGE_FILTER_LABELS[key]}
                  </option>
                ))}
              </select>
            </Field>
            {draft.stageFilter !== "any" ? (
              <Field id="hy-stage" label="Stage">
                <StageSelect
                  id="hy-stage"
                  value={draft.stage}
                  onChange={(stage) => set("stage", stage)}
                />
              </Field>
            ) : null}
          </div>
          {draft.matchers.map((matcher, index) => (
            <MatcherRow
              key={index}
              index={index}
              matcher={matcher}
              onChange={(update) => setMatcher(index, update)}
              onRemove={() =>
                onChange((current) => ({
                  ...current,
                  matchers: current.matchers.filter((_, at) => at !== index),
                }))
              }
            />
          ))}
          {draft.matchers.length < MAX_SELECT ? (
            <Pill
              type="button"
              variant="ghost"
              size="sm"
              className="justify-self-start"
              onClick={() =>
                onChange((current) => ({
                  ...current,
                  matchers: [...current.matchers, { kind: "issue_type", value: "" }],
                }))
              }
            >
              <Plus size={14} aria-hidden />
              Narrow by a type, label, title or epic
            </Pill>
          ) : null}
        </fieldset>

        <fieldset className="grid gap-3">
          <legend className="mb-1 text-[15px] font-extrabold">What is wrong</legend>
          <Field id="hy-condition" label="When">
            <select
              id="hy-condition"
              className={inputClass}
              value={kind}
              onChange={(event) => set("condition", event.target.value as HygieneConditionKind)}
            >
              {(Object.keys(CONDITION_LABELS) as HygieneConditionKind[]).map((key) => (
                <option key={key} value={key}>
                  {CONDITION_LABELS[key]}
                </option>
              ))}
            </select>
          </Field>
          {kind === "added_after_baseline_without_label" || kind === "missing_label" ? (
            <Field
              id="hy-condition-labels"
              label={kind === "missing_label" ? "Labels (any of them)" : "Change labels (optional)"}
            >
              <input
                id="hy-condition-labels"
                className={inputClass}
                value={draft.labelsText}
                placeholder={
                  kind === "missing_label" ? "team-payments" : "Empty: the scan's change labels"
                }
                onChange={(event) => set("labelsText", event.target.value)}
              />
            </Field>
          ) : null}
          {kind === "added_after_baseline_without_label" ? (
            <p className={`${hintClass} -mt-2`}>
              Needs a planning baseline: a requirement that joined after it and its grace days, with
              none of the change labels, is found.
            </p>
          ) : null}
          {kind === "missing_field" ? (
            <Field id="hy-field" label="Field">
              <select
                id="hy-field"
                className={inputClass}
                value={draft.field}
                onChange={(event) => set("field", event.target.value as HygieneIssueField)}
              >
                {(Object.keys(FIELD_LABELS) as HygieneIssueField[]).map((key) => (
                  <option key={key} value={key}>
                    {FIELD_LABELS[key]}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          {kind === "missing_gate_kind" ? (
            <Field id="hy-gate-kind" label="Gate item kind">
              <select
                id="hy-gate-kind"
                className={inputClass}
                value={draft.gateKind}
                onChange={(event) => set("gateKind", event.target.value)}
              >
                {gateKinds.map((item) => (
                  <option key={item.key} value={item.key}>
                    {item.label}
                  </option>
                ))}
              </select>
            </Field>
          ) : null}
          {kind === "stale_in_status" ? (
            <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
              <Field id="hy-condition-stage" label="In the stage">
                <StageSelect
                  id="hy-condition-stage"
                  value={draft.conditionStage}
                  onChange={(stage) => set("conditionStage", stage)}
                />
              </Field>
              <Field id="hy-days" label="Working days without a Jira change">
                <input
                  id="hy-days"
                  className={inputClass}
                  inputMode="numeric"
                  value={draft.daysText}
                  onChange={(event) => set("daysText", event.target.value)}
                />
              </Field>
            </div>
          ) : null}
          {kind === "past_due_open" ? (
            <Field id="hy-grace" label="Working days of grace after the due date">
              <input
                id="hy-grace"
                className={inputClass}
                inputMode="numeric"
                value={draft.daysText}
                onChange={(event) => set("daysText", event.target.value)}
              />
            </Field>
          ) : null}
          {kind === "too_big" ? (
            <Field id="hy-points" label="Story points above">
              <input
                id="hy-points"
                className={inputClass}
                inputMode="decimal"
                value={draft.pointsText}
                onChange={(event) => set("pointsText", event.target.value)}
              />
            </Field>
          ) : null}
        </fieldset>

        <fieldset className="grid gap-3">
          <legend className="mb-1 text-[15px] font-extrabold">Who and what then</legend>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
            <Field id="hy-owner" label="Who is asked">
              <select
                id="hy-owner"
                className={inputClass}
                value={draft.ownerKind}
                onChange={(event) => set("ownerKind", event.target.value as HygieneOwnerKind)}
              >
                {(Object.keys(OWNER_LABELS) as HygieneOwnerKind[]).map((key) => (
                  <option key={key} value={key}>
                    {OWNER_LABELS[key]}
                  </option>
                ))}
              </select>
            </Field>
            {draft.ownerKind === "profile" ? (
              <Field id="hy-owner-profile" label="Profile">
                <select
                  id="hy-owner-profile"
                  className={inputClass}
                  value={draft.ownerProfile}
                  onChange={(event) => set("ownerProfile", event.target.value)}
                >
                  <option value="">Pick a profile</option>
                  {profileOptions(catalog.data ?? [], draft.ownerProfile).map((option) => (
                    <option key={option.value} value={option.value} disabled={option.disabled}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </Field>
            ) : null}
          </div>
          <p className={`${hintClass} -mt-2`}>
            Nobody found that way: the pod&rsquo;s scrum master, else whoever decides on the
            project. A profile grants no rights.
          </p>
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-2">
            <Field id="hy-severity" label="When it finds something">
              <select
                id="hy-severity"
                className={inputClass}
                value={draft.severity}
                onChange={(event) => set("severity", event.target.value as HygieneRuleSeverity)}
              >
                <option value="list_only">List it on Overall and count it</option>
                <option value="ask">Also ask the owner in the day report</option>
              </select>
            </Field>
            {draft.severity === "ask" ? (
              <Field id="hy-need" label="The ask">
                <select
                  id="hy-need"
                  className={inputClass}
                  value={draft.need}
                  onChange={(event) => set("need", event.target.value as HygieneRuleNeed)}
                >
                  <option value="fix">A fix</option>
                  <option value="decision">A decision</option>
                </select>
              </Field>
            ) : null}
          </div>
          {draft.severity === "ask" ? (
            <p className={`${hintClass} -mt-2`}>
              One ask per owner, naming up to five issues; it climbs the escalation matrix the
              longer it waits.
            </p>
          ) : null}
          <Field id="hy-fix" label="Suggested fix">
            <input
              id="hy-fix"
              className={inputClass}
              value={draft.suggestedFix}
              maxLength={300}
              placeholder="Estimate {key} before it starts."
              onChange={(event) => set("suggestedFix", event.target.value)}
            />
          </Field>
          <p className={`${hintClass} -mt-2`}>
            Words it fills in: {FIX_PLACEHOLDERS.map((name) => `{${name}}`).join(" ")}. Empty:
            OpenProgram&rsquo;s own words for the rule.
          </p>
          <label className="flex items-center gap-1.5 text-[14px] font-bold">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(event) => set("enabled", event.target.checked)}
            />
            On: check issues against it
          </label>
        </fieldset>

        <TryOnProject rule={ruleFromDraft(draft)} disabled={problems.length > 0} />
        {attempted ? <Problems problems={problems} /> : null}
        <div className="flex justify-end gap-2">
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save rule"}
          </Pill>
        </div>
      </form>
    </AdminDialog>
  );
}

function StageSelect({
  id,
  value,
  onChange,
}: {
  id: string;
  value: DeliveryStage;
  onChange: (stage: DeliveryStage) => void;
}) {
  return (
    <select
      id={id}
      className={inputClass}
      value={value}
      onChange={(event) => onChange(event.target.value as DeliveryStage)}
    >
      {STEPS.map((step) => (
        <option key={step} value={step}>
          {STAGE_LABELS[step]}
        </option>
      ))}
    </select>
  );
}

function MatcherRow({
  index,
  matcher,
  onChange,
  onRemove,
}: {
  index: number;
  matcher: SelectDraft;
  onChange: (update: (matcher: SelectDraft) => SelectDraft) => void;
  onRemove: () => void;
}) {
  const id = `hy-matcher-${index}`;
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] items-end gap-2 rounded-2xl border border-grey-border p-3 sm:grid-cols-[160px_minmax(0,1fr)_auto]">
      <div>
        <label htmlFor={`${id}-kind`} className={labelClass}>
          Narrow by
        </label>
        <select
          id={`${id}-kind`}
          className={inputClass}
          value={matcher.kind}
          onChange={(event) => {
            const kind = event.target.value as ReadinessMatcherKind;
            onChange((current) => ({ ...current, kind }));
          }}
        >
          {(Object.keys(SELECT_LABELS) as ReadinessMatcherKind[]).map((kind) => (
            <option key={kind} value={kind}>
              {SELECT_LABELS[kind]}
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
      <button
        type="button"
        aria-label={`Remove matcher ${index + 1}`}
        className="mb-1 justify-self-start rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
        onClick={onRemove}
      >
        <X size={16} aria-hidden />
      </button>
    </div>
  );
}

/** What the scan would find on one project with the rule as it stands. Saves nothing. */
function TryOnProject({ rule, disabled }: { rule: HygieneRuleDto; disabled: boolean }) {
  const projects = useProjects();
  const [projectId, setProjectId] = useState("");
  const [result, setResult] = useState<HygienePreviewResponse | null>(null);
  const preview = useMutation({
    mutationFn: (id: string) => apiClient.previewHygieneRule(id, rule),
    onSuccess: setResult,
    onError: (error) => toast.error(errorText(error)),
  });
  const chosen = projectId || projects.data?.[0]?.id || "";
  return (
    <div className="grid gap-2 rounded-2xl bg-grey-fill px-4 py-3">
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-[1_1_200px]">
          <label htmlFor="hy-try" className={labelClass}>
            Try on a project
          </label>
          <select
            id="hy-try"
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
        result.scopes.length === 0 ? (
          <p className="text-[13px] text-grey-body">It reads no scope of this project.</p>
        ) : (
          <div className="grid gap-2 text-[13px]">
            {result.scopes.map((row) => (
              <div key={`${row.scope.kind}-${row.scope.id}`} className="grid gap-1">
                <p className="font-bold text-ink">{previewLine(row)}</p>
                {row.issues.length > 0 ? (
                  <ul className="grid gap-1 pl-1">
                    {row.issues.map((issue) => (
                      <li key={issue.key} className="min-w-0 text-grey-body">
                        <b className="text-ink">{issue.key}</b> {issue.title}: {issue.what}
                        {issue.owner_name ? ` · asks ${issue.owner_name}` : ""}
                      </li>
                    ))}
                    {row.found > row.issues.length ? (
                      <li className="text-grey-secondary">
                        and {row.found - row.issues.length} more
                      </li>
                    ) : null}
                  </ul>
                ) : null}
              </div>
            ))}
            <p className="text-[12px] text-grey-secondary">
              Nothing was saved, and nobody was asked.
            </p>
          </div>
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
