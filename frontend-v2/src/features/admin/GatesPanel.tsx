import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { DeliveryStage, GateTemplateDto } from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { TextInput } from "../../components/ui/Field";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import {
  MAX_KINDS,
  ROLE_NAMES,
  SIGN_OFF_ROLES,
  type KindDraft,
  type TemplateDraft,
  draftFromTemplate,
  draftProblems,
  newKindDraft,
  newTemplateDraft,
  signOffLine,
  templateFromDraft,
  toggleRole,
  withLabel,
} from "../gates/gates";
import { STAGE_COLORS, STAGE_LABELS, STAGES } from "../requirements/stages";
import { AdminSelect } from "./AdminSelect";
import { ConfirmDialog } from "./ConfirmDialog";
import { FormField } from "./FormField";
import { type ConfirmState, errorMessage } from "./adminTypes";

const GATES_QUERY_KEY = ["config", "gates"] as const;

/**
 * The gates a requirement passes on its way to production.
 *
 * Each gate guards one delivery stage and lists the kinds of item it needs,
 * who signs each kind off, and the headings under which Jira issues write
 * them, so a scan can suggest them.
 */
export function GatesPanel() {
  const queryClient = useQueryClient();
  const gates = useQuery({ queryKey: GATES_QUERY_KEY, queryFn: apiClient.gateTemplates });
  const [editing, setEditing] = useState<TemplateDraft | null>(null);
  const [confirm, setConfirm] = useState<ConfirmState>({ open: false });
  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: GATES_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: ["persona", "gates"] }),
    ]);
  const remove = useMutation({
    mutationFn: (templateId: string) => apiClient.removeGateTemplate(templateId),
    onSuccess: async () => {
      await invalidate();
      toast.success("Gate removed.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (gates.isError) {
    return <p className="text-[14px] text-rag-red">{errorMessage(gates.error)}</p>;
  }
  if (!gates.data) {
    return (
      <Card padding="px-5 py-10" className="text-center text-[14px] text-grey-secondary">
        Loading gates…
      </Card>
    );
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-bold">Gates</h2>
          <p className="mt-1 max-w-[680px] text-[13px] text-grey-secondary">
            What a requirement must pass before it enters a stage, such as the business&apos;s
            acceptance criteria before production. Items are read from Jira descriptions and
            comments as suggestions, and count once someone confirms them.
            {gates.data.is_default ? " These are the defaults until you change one." : ""}
          </p>
        </div>
        <Pill size="sm" onClick={() => setEditing(newTemplateDraft())}>
          <Plus size={14} />
          Add gate
        </Pill>
      </div>

      {gates.data.templates.length === 0 ? (
        <Card padding="p-5" className="text-[14px] text-grey-secondary">
          No gates. Add one to track acceptance criteria or test cases per requirement.
        </Card>
      ) : (
        <div className="grid gap-3 lg:grid-cols-2">
          {gates.data.templates.map((template) => (
            <TemplateCard
              key={template.template_id}
              template={template}
              onEdit={() => setEditing(draftFromTemplate(template))}
              onRemove={() =>
                setConfirm({
                  open: true,
                  title: `Remove ${template.name}?`,
                  description:
                    "Requirements stop being checked against this gate. Items already confirmed stay stored.",
                  confirmLabel: "Remove gate",
                  destructive: true,
                  onConfirm: () => remove.mutate(template.template_id ?? ""),
                })
              }
            />
          ))}
        </div>
      )}

      {editing ? (
        <TemplateDialog
          draft={editing}
          onChange={(update) => setEditing((current) => (current ? update(current) : current))}
          onClose={() => setEditing(null)}
          onSaved={invalidate}
        />
      ) : null}
      <ConfirmDialog
        state={confirm}
        onOpenChange={(open) => (open ? undefined : setConfirm({ open: false }))}
      />
    </div>
  );
}

function TemplateCard({
  template,
  onEdit,
  onRemove,
}: {
  template: GateTemplateDto;
  onEdit: () => void;
  onRemove: () => void;
}) {
  return (
    <Card padding="p-5" className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2">
            <h3 className="text-[16px] font-bold">{template.name}</h3>
            {template.enabled === false ? (
              <RagChip tone="neutral" className="h-6 text-[12px]">
                Off
              </RagChip>
            ) : null}
          </div>
          <p className="mt-0.5 flex items-center gap-1.5 text-[13px] text-grey-secondary">
            <span
              aria-hidden
              className="inline-block h-2.5 w-2.5 rounded-full"
              style={{ backgroundColor: STAGE_COLORS[template.guards_stage] }}
            />
            Passed before {STAGE_LABELS[template.guards_stage].toLowerCase()}
            {" · "}
            {(template.issue_types ?? []).length > 0
              ? (template.issue_types ?? []).join(", ")
              : "every requirement"}
          </p>
        </div>
        <div className="flex gap-1">
          <button
            type="button"
            aria-label={`Edit ${template.name}`}
            className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
            onClick={onEdit}
          >
            <Pencil size={14} />
          </button>
          <button
            type="button"
            aria-label={`Remove ${template.name}`}
            className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
            onClick={onRemove}
          >
            <Trash2 size={14} />
          </button>
        </div>
      </div>
      <ul className="flex flex-col gap-2">
        {template.kinds.map((kind) => (
          <li key={kind.key} className="rounded-xl bg-grey-fill px-3 py-2">
            <div className="text-[14px] font-bold">{kind.label}</div>
            <div className="text-[12px] text-grey-secondary">{signOffLine(kind)}</div>
            {(kind.headings ?? []).length > 0 || kind.gherkin ? (
              <div className="mt-1 text-[12px] text-grey-body">
                Read from{" "}
                {[
                  ...(kind.headings ?? []).map((heading) => `"${heading}"`),
                  ...(kind.gherkin ? ["Given/When/Then scenarios"] : []),
                ].join(", ")}
              </div>
            ) : null}
          </li>
        ))}
      </ul>
    </Card>
  );
}

function TemplateDialog({
  draft,
  onChange,
  onClose,
  onSaved,
}: {
  draft: TemplateDraft;
  /** Changes are applied to the latest draft, so quick edits never undo each other. */
  onChange: (update: (draft: TemplateDraft) => TemplateDraft) => void;
  onClose: () => void;
  onSaved: () => Promise<unknown>;
}) {
  const save = useMutation({
    mutationFn: () => apiClient.saveGateTemplate(templateFromDraft(draft)),
    onSuccess: async () => {
      await onSaved();
      toast.success("Gate saved.");
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const [attempted, setAttempted] = useState(false);
  const problems = draftProblems(draft);
  const setKind = (index: number, update: (kind: KindDraft) => KindDraft) =>
    onChange((current) => ({
      ...current,
      kinds: current.kinds.map((item, at) => (at === index ? update(item) : item)),
    }));

  return (
    <Modal
      open
      wide
      onOpenChange={(open) => (open ? undefined : onClose())}
      title={draft.templateId ? `Edit ${draft.name || "gate"}` : "Add a gate"}
    >
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setAttempted(true);
          if (problems.length === 0) save.mutate();
        }}
      >
        <div className="grid gap-3 sm:grid-cols-2">
          <FormField label="Name" htmlFor="gate-name">
            <TextInput
              id="gate-name"
              value={draft.name}
              maxLength={120}
              placeholder="Security review"
              onChange={(event) => {
                const name = event.target.value;
                onChange((current) => ({ ...current, name }));
              }}
            />
          </FormField>
          <FormField label="Passed before" htmlFor="gate-stage">
            <AdminSelect
              id="gate-stage"
              value={draft.guardsStage}
              onChange={(event) => {
                const guardsStage = event.target.value as DeliveryStage;
                onChange((current) => ({ ...current, guardsStage }));
              }}
            >
              {STAGES.map((stage) => (
                <option key={stage} value={stage}>
                  {STAGE_LABELS[stage]}
                </option>
              ))}
            </AdminSelect>
          </FormField>
        </div>
        <FormField label="Issue types (optional)" htmlFor="gate-types">
          <TextInput
            id="gate-types"
            value={draft.issueTypesText}
            placeholder="Story, Epic"
            onChange={(event) => {
              const issueTypesText = event.target.value;
              onChange((current) => ({ ...current, issueTypesText }));
            }}
          />
        </FormField>
        <p className="-mt-3 text-[12px] text-grey-secondary">
          Separated by commas. Leave it empty to check every requirement.
        </p>
        <label className="flex items-center gap-2 text-[14px] font-bold">
          <input
            type="checkbox"
            checked={draft.enabled}
            onChange={(event) => {
              const enabled = event.target.checked;
              onChange((current) => ({ ...current, enabled }));
            }}
          />
          Check requirements against this gate
        </label>

        <div className="flex flex-col gap-3">
          <h4 className="text-[15px] font-bold">What it needs</h4>
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
              variant="ghost"
              size="sm"
              className="self-start"
              onClick={() =>
                onChange((current) => ({ ...current, kinds: [...current.kinds, newKindDraft()] }))
              }
            >
              <Plus size={14} />
              Add a kind of item
            </Pill>
          ) : null}
        </div>

        {attempted && problems.length > 0 ? (
          <ul className="text-[12px] text-rag-red">
            {problems.map((problem) => (
              <li key={problem}>{problem}</li>
            ))}
          </ul>
        ) : null}
        <div className="flex justify-end gap-2">
          <Pill variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill
            type="submit"
            size="sm"
            disabled={(attempted && problems.length > 0) || save.isPending}
          >
            {save.isPending ? "Saving…" : "Save gate"}
          </Pill>
        </div>
      </form>
    </Modal>
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
    <fieldset className="flex flex-col gap-3 rounded-2xl border border-grey-border p-4">
      <div className="flex items-end gap-2">
        <div className="flex-1">
          <FormField label="Kind of item" htmlFor={`${id}-label`}>
            <TextInput
              id={`${id}-label`}
              value={kind.label}
              maxLength={80}
              placeholder="Acceptance criterion"
              onChange={(event) => {
                const label = event.target.value;
                onChange((current) => withLabel(current, label));
              }}
            />
          </FormField>
        </div>
        {onRemove ? (
          <button
            type="button"
            aria-label={`Remove ${kind.label || "this kind"}`}
            className="mb-2 rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
            onClick={onRemove}
          >
            <X size={16} />
          </button>
        ) : null}
      </div>
      <div>
        <span className="text-[13px] font-bold">Signed off by</span>
        <div className="mt-1 flex flex-wrap gap-3">
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
        <p className="mt-1 text-[12px] text-grey-secondary">An admin may always sign off.</p>
      </div>
      <FormField label="Headings it is written under in Jira" htmlFor={`${id}-headings`}>
        <TextInput
          id={`${id}-headings`}
          value={kind.headingsText}
          placeholder="Acceptance criteria, AC, Definition of done"
          onChange={(event) => {
            const headingsText = event.target.value;
            onChange((current) => ({ ...current, headingsText }));
          }}
        />
      </FormField>
      <div className="flex flex-wrap gap-4">
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
          Read Given/When/Then scenarios
        </label>
      </div>
    </fieldset>
  );
}
