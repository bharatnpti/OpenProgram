import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { DeliveryStage, GateTemplateDto, GateTemplatesResponse } from "../../api/schema";
import { ConfirmDialog } from "../../components/Dialogs";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { STAGE_LABELS, stageColor } from "../../lib/status";
import { AdminDialog, Problems, TabIntro, hintClass, inputClass, labelClass } from "./AdminBits";
import { errorText } from "./adminWords";
import {
  MAX_KINDS,
  ROLE_NAMES,
  SIGN_OFF_ROLES,
  type KindDraft,
  type TemplateDraft,
  appliesToLine,
  draftFromTemplate,
  draftProblems,
  newKindDraft,
  newTemplateDraft,
  readFromLine,
  signOffLine,
  templateFromDraft,
  toggleRole,
  withLabel,
} from "./gateForm";
import { STEPS } from "./stageMapping";

const GATES_KEY = ["config", "gates"] as const;

/**
 * The gates a requirement passes on its way to production: what each checks,
 * before which step, and who signs each item off. Items are suggested from
 * Jira under the headings a gate names, and count once a person keeps them.
 */
export function GatesTab() {
  const queryClient = useQueryClient();
  const gates = useQuery({ queryKey: GATES_KEY, queryFn: () => apiClient.gateTemplates() });
  const [editing, setEditing] = useState<TemplateDraft | null>(null);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: GATES_KEY });
    // Overall's gate board and the day report read these gates.
    void queryClient.invalidateQueries({ queryKey: ["gates"] });
    void queryClient.invalidateQueries({ queryKey: ["day-reports"] });
  };
  const remove = useMutation({
    mutationFn: (templateId: string) => apiClient.removeGateTemplate(templateId),
    onSuccess: () => {
      toast.success("Gate removed. Requirements are no longer checked against it.");
      refresh();
    },
    onError: (error) => toast.error(errorText(error)),
  });

  return (
    <PanelState
      isLoading={gates.isLoading}
      error={gates.error}
      onRetry={() => void gates.refetch()}
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <TabIntro>
            A gate is a check a requirement should pass before it moves into a step, such as the
            business accepting it before Production. Each gate lists the items it needs and who
            signs each one off. Items are suggested from the Jira issue's text, and count once a
            person keeps them.
          </TabIntro>
          <Pill size="sm" onClick={() => setEditing(newTemplateDraft())}>
            <Plus size={14} aria-hidden />
            Add a gate
          </Pill>
        </div>
        {gates.data?.is_default ? (
          <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
            These are the two gates every tenant starts with. Change or remove one and the set
            becomes this tenant's own.
          </p>
        ) : null}
        {(gates.data?.templates ?? []).length === 0 ? (
          <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[13px] text-grey-body">
            No gates. Add one to check acceptance criteria or test cases per requirement.
          </p>
        ) : (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-2">
            {(gates.data?.templates ?? []).map((template) => (
              <GateCard
                key={template.template_id}
                template={template}
                removeText={removeText(gates.data!, template)}
                onEdit={() => setEditing(draftFromTemplate(template))}
                onRemove={() => remove.mutate(template.template_id ?? "")}
              />
            ))}
          </div>
        )}
      </div>
      {editing ? (
        <GateDialog
          draft={editing}
          onChange={(update) => setEditing((current) => (current ? update(current) : current))}
          onClose={() => setEditing(null)}
          onSaved={refresh}
        />
      ) : null}
    </PanelState>
  );
}

/** What removing a gate does, which depends on whether the defaults are still in use. */
function removeText(gates: GateTemplatesResponse, template: GateTemplateDto): string {
  const base = `Requirements stop being checked against ${template.name}.`;
  if (gates.is_default) return `${base} The other gates stay, saved as this tenant's own.`;
  if (gates.templates.length === 1) {
    return `${base} It is the last gate, so the two default gates come back.`;
  }
  return `${base} Items people already kept stay stored.`;
}

function GateCard({
  template,
  removeText,
  onEdit,
  onRemove,
}: {
  template: GateTemplateDto;
  removeText: string;
  onEdit: () => void;
  onRemove: () => void;
}) {
  return (
    <section className="grid min-w-0 content-start gap-3 rounded-3xl border border-grey-border bg-white p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-[17px] font-extrabold">{template.name}</h3>
            {template.enabled === false ? (
              <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                off
              </RagChip>
            ) : null}
          </div>
          <p className="mt-0.5 flex flex-wrap items-center gap-1.5 text-[13px] text-grey-body">
            <span
              aria-hidden
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: stageColor(template.guards_stage) }}
            />
            Passed before {STAGE_LABELS[template.guards_stage]} · {appliesToLine(template)}
          </p>
        </div>
        <div className="flex flex-none gap-1">
          <button
            type="button"
            aria-label={`Change ${template.name}`}
            className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
            onClick={onEdit}
          >
            <Pencil size={15} aria-hidden />
          </button>
          <ConfirmDialog
            trigger={
              <button
                type="button"
                aria-label={`Remove ${template.name}`}
                className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
              >
                <Trash2 size={15} aria-hidden />
              </button>
            }
            title={`Remove ${template.name}?`}
            description={removeText}
            confirmLabel="Remove gate"
            onConfirm={onRemove}
          />
        </div>
      </div>
      <ul className="grid gap-2">
        {template.kinds.map((kind) => {
          const readFrom = readFromLine(kind);
          return (
            <li key={kind.key} className="rounded-2xl bg-grey-fill px-3 py-2">
              <p className="text-[14px] font-bold">Checks: {kind.label}</p>
              <p className="text-[12px] text-grey-body">{signOffLine(kind)}</p>
              {readFrom ? <p className="text-[12px] text-grey-secondary">{readFrom}</p> : null}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

function GateDialog({
  draft,
  onChange,
  onClose,
  onSaved,
}: {
  draft: TemplateDraft;
  /** Changes apply to the latest draft, so quick edits never undo each other. */
  onChange: (update: (draft: TemplateDraft) => TemplateDraft) => void;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [attempted, setAttempted] = useState(false);
  const problems = draftProblems(draft);
  const save = useMutation({
    mutationFn: () => apiClient.saveGateTemplate(templateFromDraft(draft)),
    onSuccess: (saved) => {
      toast.success(`${saved.name} saved. Requirements are checked against it from now on.`);
      onSaved();
      onClose();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const setKind = (index: number, update: (kind: KindDraft) => KindDraft) =>
    onChange((current) => ({
      ...current,
      kinds: current.kinds.map((item, at) => (at === index ? update(item) : item)),
    }));

  return (
    <AdminDialog
      open
      wide
      onOpenChange={(open) => (open ? undefined : onClose())}
      title={draft.templateId ? `Change ${draft.name || "the gate"}` : "Add a gate"}
      description="A saved gate shows on Overall at once; its items are suggested from the next read of Jira."
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
          <div>
            <label htmlFor="gate-name" className={labelClass}>
              Name
            </label>
            <input
              id="gate-name"
              className={inputClass}
              value={draft.name}
              maxLength={120}
              placeholder="Security review"
              onChange={(event) => {
                const name = event.target.value;
                onChange((current) => ({ ...current, name }));
              }}
            />
          </div>
          <div>
            <label htmlFor="gate-stage" className={labelClass}>
              Passed before
            </label>
            <select
              id="gate-stage"
              className={inputClass}
              value={draft.guardsStage}
              onChange={(event) => {
                const guardsStage = event.target.value as DeliveryStage;
                onChange((current) => ({ ...current, guardsStage }));
              }}
            >
              {STEPS.map((step, index) => (
                <option key={step} value={step}>
                  {index + 1}. {STAGE_LABELS[step]}
                </option>
              ))}
            </select>
          </div>
        </div>
        <div>
          <label htmlFor="gate-types" className={labelClass}>
            Applies to (optional)
          </label>
          <input
            id="gate-types"
            className={inputClass}
            value={draft.issueTypesText}
            placeholder="Every requirement. Story, Epic…"
            onChange={(event) => {
              const issueTypesText = event.target.value;
              onChange((current) => ({ ...current, issueTypesText }));
            }}
          />
          <p className={hintClass}>
            Issue types, separated by commas. Empty checks every requirement.
          </p>
        </div>
        <label className="flex items-center gap-2 text-[14px] font-bold">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => {
              const enabled = event.target.checked;
              onChange((current) => ({ ...current, enabled }));
            }}
          />
          On: check requirements against this gate
        </label>

        <fieldset className="grid gap-3">
          <legend className="mb-1 text-[15px] font-extrabold">What it checks</legend>
          {draft.kinds.map((kind, index) => (
            <KindEditor
              key={index}
              index={index}
              kind={kind}
              onChange={(update) => setKind(index, update)}
              onRemove={
                draft.kinds.length > 1
                  ? () =>
                      onChange((current) => ({
                        ...current,
                        kinds: current.kinds.filter((_, at) => at !== index),
                      }))
                  : undefined
              }
            />
          ))}
          {draft.kinds.length < MAX_KINDS ? (
            <Pill
              type="button"
              variant="ghost"
              size="sm"
              className="justify-self-start"
              onClick={() =>
                onChange((current) => ({ ...current, kinds: [...current.kinds, newKindDraft()] }))
              }
            >
              <Plus size={14} aria-hidden />
              Add an item to check
            </Pill>
          ) : null}
        </fieldset>

        {attempted ? <Problems problems={problems} /> : null}
        <div className="flex justify-end gap-2">
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={save.isPending}>
            {save.isPending ? "Saving…" : "Save gate"}
          </Pill>
        </div>
      </form>
    </AdminDialog>
  );
}

function KindEditor({
  index,
  kind,
  onChange,
  onRemove,
}: {
  index: number;
  kind: KindDraft;
  onChange: (update: (kind: KindDraft) => KindDraft) => void;
  onRemove?: () => void;
}) {
  const id = `gate-kind-${index}`;
  return (
    <div className="grid gap-3 rounded-2xl border border-grey-border p-4">
      <div className="flex items-end gap-2">
        <div className="min-w-0 flex-1">
          <label htmlFor={`${id}-label`} className={labelClass}>
            Item {index + 1}
          </label>
          <input
            id={`${id}-label`}
            className={inputClass}
            value={kind.label}
            maxLength={80}
            placeholder="Acceptance criterion"
            onChange={(event) => {
              const label = event.target.value;
              onChange((current) => withLabel(current, label));
            }}
          />
        </div>
        {onRemove ? (
          <button
            type="button"
            aria-label={`Remove ${kind.label || `item ${index + 1}`}`}
            className="mb-1 rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
            onClick={onRemove}
          >
            <X size={16} aria-hidden />
          </button>
        ) : null}
      </div>
      <fieldset>
        <legend className={labelClass}>Signed off by</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {SIGN_OFF_ROLES.map((role) => (
            <label key={role} className="flex items-center gap-1.5 text-[14px]">
              <input
                type="checkbox"
                checked={kind.signOffRoles.includes(role)}
                onChange={() => onChange((current) => toggleRole(current, role))}
              />
              {ROLE_NAMES[role]}
            </label>
          ))}
        </div>
        <p className={hintClass}>An admin may always sign off.</p>
      </fieldset>
      <div>
        <label htmlFor={`${id}-headings`} className={labelClass}>
          Headings it is written under in Jira
        </label>
        <input
          id={`${id}-headings`}
          className={inputClass}
          value={kind.headingsText}
          placeholder="Acceptance criteria, AC, Definition of done"
          onChange={(event) => {
            const headingsText = event.target.value;
            onChange((current) => ({ ...current, headingsText }));
          }}
        />
        <p className={hintClass}>
          Lines under these headings in an issue's description or comments are suggested as items.
        </p>
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-2">
        <label className="flex items-center gap-1.5 text-[14px]">
          <input
            type="checkbox"
            checked={kind.evidenceRequired}
            onChange={(event) => {
              const evidenceRequired = event.target.checked;
              onChange((current) => ({ ...current, evidenceRequired }));
            }}
          />
          Met only with a link to the evidence
        </label>
        <label className="flex items-center gap-1.5 text-[14px]">
          <input
            type="checkbox"
            checked={kind.gherkin}
            onChange={(event) => {
              const gherkin = event.target.checked;
              onChange((current) => ({ ...current, gherkin }));
            }}
          />
          Also read Given/When/Then scenarios
        </label>
      </div>
    </div>
  );
}
