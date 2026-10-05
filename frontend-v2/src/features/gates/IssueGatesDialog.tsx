import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, ExternalLink, Plus, X } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  GateBoardResponse,
  GateItemResponse,
  GateTemplateDto,
  IssueGatesResponse,
  ItemKindDto,
  ItemStatus,
} from "../../api/schema";
import { useRole } from "../../app/role";
import { useViewingDate } from "../../app/viewingDate";
import { TextInput } from "../../components/ui/Field";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { AdminSelect } from "../admin/AdminSelect";
import { errorMessage } from "../admin/adminTypes";
import { dayLabel } from "../forecast/forecast";
import { STAGE_LABELS } from "../requirements/stages";
import {
  GATE_STATE_LABELS,
  GATE_STATE_TONES,
  ITEM_STATUS_LABELS,
  ITEM_STATUS_TONES,
  SOURCE_LABELS,
  evaluationFor,
  evidenceProblem,
  itemsByKind,
  maySignOff,
  passedWithoutLine,
  signOffLine,
  withArticle,
} from "./gates";

const SIGN_OFF_CHOICES: ItemStatus[] = ["met", "failed", "waived", "pending"];

/**
 * One requirement against each gate that applies to it: the suggestions read
 * from Jira to confirm or dismiss, the confirmed items to sign off, and a way
 * to add one by hand.
 */
export function IssueGatesDialog({
  board,
  issueKey,
  projectId,
  asOf,
  onClose,
}: {
  board: GateBoardResponse;
  issueKey: string;
  projectId: string;
  asOf: string;
  onClose: () => void;
}) {
  const issue = board.issues.find((item) => item.key === issueKey);
  if (!issue) return null;
  const templates = board.templates.filter((template) =>
    evaluationFor(issue, template.template_id),
  );
  return (
    <Modal
      open
      wide
      onOpenChange={(open) => (open ? undefined : onClose())}
      title={`${issue.key}: ${issue.title}`}
    >
      <div className="flex flex-col gap-5">
        <p className="text-[13px] text-grey-secondary">
          {STAGE_LABELS[issue.stage]}
          {issue.status ? ` · Jira status ${issue.status}` : ""} · viewing {dayLabel(asOf)}
        </p>
        {issue.passed_without.length > 0 ? (
          <div
            role="status"
            className="flex items-start gap-2 rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red"
          >
            <CircleAlert size={14} className="mt-0.5 shrink-0" />
            {passedWithoutLine(issue, STAGE_LABELS[issue.stage])}
          </div>
        ) : null}
        {templates.length === 0 ? (
          <p className="text-[14px] text-grey-secondary">No gate applies to this issue type.</p>
        ) : (
          templates.map((template) => (
            <TemplateSection
              key={template.template_id}
              issue={issue}
              template={template}
              projectId={projectId}
              names={board.actor_names ?? {}}
            />
          ))
        )}
        <div className="flex justify-end">
          <Pill variant="ghost" size="sm" onClick={onClose}>
            Close
          </Pill>
        </div>
      </div>
    </Modal>
  );
}

function TemplateSection({
  issue,
  template,
  projectId,
  names,
}: {
  issue: IssueGatesResponse;
  template: GateTemplateDto;
  projectId: string;
  names: Record<string, string>;
}) {
  const evaluation = evaluationFor(issue, template.template_id);
  return (
    <section className="flex flex-col gap-3 rounded-2xl border border-grey-border p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h4 className="text-[16px] font-bold">{template.name}</h4>
          <p className="text-[12px] text-grey-secondary">
            Passed before {STAGE_LABELS[template.guards_stage].toLowerCase()}
          </p>
        </div>
        {evaluation ? (
          <RagChip tone={GATE_STATE_TONES[evaluation.state]} className="h-6 text-[12px]">
            {GATE_STATE_LABELS[evaluation.state]}
            {evaluation.total > 0 ? ` · ${evaluation.met} of ${evaluation.total} met` : ""}
          </RagChip>
        ) : null}
      </div>
      {itemsByKind(issue, template).map((group) => (
        <KindBlock
          key={group.kind.key}
          issueKey={issue.key}
          templateId={template.template_id ?? ""}
          kind={group.kind}
          suggested={group.suggested}
          confirmed={group.confirmed}
          projectId={projectId}
          names={names}
        />
      ))}
    </section>
  );
}

function useGateInvalidate(projectId: string) {
  const queryClient = useQueryClient();
  return () => queryClient.invalidateQueries({ queryKey: ["persona", "gates", projectId] });
}

function KindBlock({
  issueKey,
  templateId,
  kind,
  suggested,
  confirmed,
  projectId,
  names,
}: {
  issueKey: string;
  templateId: string;
  kind: ItemKindDto;
  suggested: GateItemResponse[];
  confirmed: GateItemResponse[];
  projectId: string;
  names: Record<string, string>;
}) {
  const { lensRoles, ...role } = useRole();
  const { isPast } = useViewingDate();
  const canEditGates = role.canEditGates && !isPast;
  const invalidate = useGateInvalidate(projectId);
  const [text, setText] = useState("");
  const decide = useMutation({
    mutationFn: ({ item, keep }: { item: GateItemResponse; keep: boolean }) =>
      keep ? apiClient.confirmGateItem(item.item_id) : apiClient.dismissGateItem(item.item_id),
    onSuccess: async () => {
      await invalidate();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const add = useMutation({
    mutationFn: () =>
      apiClient.addGateItem(issueKey, {
        template_id: templateId,
        kind: kind.key,
        text: text.trim(),
      }),
    onSuccess: async () => {
      setText("");
      await invalidate();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const signs = canEditGates && maySignOff(kind, lensRoles);

  return (
    <div className="flex flex-col gap-2">
      <div>
        <h5 className="text-[14px] font-bold">{kind.label}</h5>
        <p className="text-[12px] text-grey-secondary">{signOffLine(kind)}</p>
      </div>

      {suggested.length > 0 ? (
        <div className="rounded-xl bg-grey-fill px-3 py-2">
          <p className="text-[12px] font-bold text-grey-secondary">
            Read from Jira. Keep the ones that belong.
          </p>
          <ul className="mt-1 flex flex-col gap-1.5">
            {suggested.map((item) => (
              <li key={item.item_id} className="flex items-start justify-between gap-3">
                <span className="text-[14px]">
                  {item.text}
                  <span className="block text-[12px] text-grey-secondary">
                    {SOURCE_LABELS[item.source]}
                  </span>
                </span>
                {canEditGates ? (
                  <span className="flex shrink-0 items-center gap-1">
                    <Pill
                      size="sm"
                      variant="ghost"
                      className="h-8 px-3"
                      disabled={decide.isPending}
                      onClick={() => decide.mutate({ item, keep: true })}
                    >
                      <Check size={13} />
                      Keep
                    </Pill>
                    <button
                      type="button"
                      aria-label={`Dismiss: ${item.text}`}
                      className="rounded-full p-2 text-grey-secondary hover:bg-white hover:text-ink"
                      disabled={decide.isPending}
                      onClick={() => decide.mutate({ item, keep: false })}
                    >
                      <X size={14} />
                    </button>
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {confirmed.length === 0 ? (
        <p className="text-[13px] text-grey-secondary">
          No {kind.label.toLowerCase()} confirmed yet.
        </p>
      ) : (
        <ul className="flex flex-col divide-y divide-grey-border">
          {confirmed.map((item) => (
            <ItemRow
              key={item.item_id}
              item={item}
              kind={kind}
              signs={signs}
              projectId={projectId}
              names={names}
            />
          ))}
        </ul>
      )}

      {canEditGates ? (
        <form
          className="flex gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (text.trim()) add.mutate();
          }}
        >
          <TextInput
            aria-label={`Add ${withArticle(kind.label)}`}
            className="py-2 text-[14px]"
            value={text}
            maxLength={1000}
            placeholder={`Add ${withArticle(kind.label)}`}
            onChange={(event) => setText(event.target.value)}
          />
          <Pill type="submit" variant="ghost" size="sm" disabled={!text.trim() || add.isPending}>
            <Plus size={14} />
            Add
          </Pill>
        </form>
      ) : null}
    </div>
  );
}

function ItemRow({
  item,
  kind,
  signs,
  projectId,
  names,
}: {
  item: GateItemResponse;
  kind: ItemKindDto;
  signs: boolean;
  projectId: string;
  names: Record<string, string>;
}) {
  const invalidate = useGateInvalidate(projectId);
  const [editing, setEditing] = useState(false);
  const [status, setStatus] = useState<ItemStatus>(item.status === "pending" ? "met" : item.status);
  const [evidence, setEvidence] = useState(item.evidence_url ?? "");
  const [note, setNote] = useState(item.note);
  const save = useMutation({
    mutationFn: () =>
      apiClient.signOffGateItem(item.item_id, {
        status,
        evidence_url: evidence.trim() || null,
        note: note.trim(),
      }),
    onSuccess: async () => {
      setEditing(false);
      await invalidate();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const problem = evidenceProblem(evidence, kind.evidence_required ?? false, status);
  const signer = item.signed_by ? (names[item.signed_by] ?? item.signed_by) : null;

  return (
    <li className="flex flex-col gap-2 py-2">
      <div className="flex items-start justify-between gap-3">
        <span className="text-[14px]">
          {item.text}
          <span className="block text-[12px] text-grey-secondary">
            {signer && item.signed_at && item.status !== "pending"
              ? `${ITEM_STATUS_LABELS[item.status]} by ${signer}, ${dayLabel(item.signed_at.slice(0, 10))}`
              : SOURCE_LABELS[item.source]}
            {item.note ? ` · ${item.note}` : ""}
          </span>
          {item.evidence_url ? (
            <a
              href={item.evidence_url}
              target="_blank"
              rel="noreferrer"
              className="mt-0.5 inline-flex items-center gap-1 text-[12px] font-bold text-magenta hover:underline"
            >
              Evidence <ExternalLink size={11} />
            </a>
          ) : null}
        </span>
        <span className="flex shrink-0 items-center gap-2">
          <RagChip tone={ITEM_STATUS_TONES[item.status]} className="h-6 text-[12px]">
            {ITEM_STATUS_LABELS[item.status]}
          </RagChip>
          {signs && !editing ? (
            <button
              type="button"
              className="text-[13px] font-bold text-magenta hover:underline"
              onClick={() => setEditing(true)}
            >
              Sign off
            </button>
          ) : null}
        </span>
      </div>
      {editing ? (
        <form
          className="flex flex-col gap-2 rounded-xl bg-grey-fill p-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!problem) save.mutate();
          }}
        >
          <div className="grid gap-2 sm:grid-cols-[160px_1fr]">
            <AdminSelect
              aria-label="Sign off as"
              className="py-2 text-[14px]"
              value={status}
              onChange={(event) => setStatus(event.target.value as ItemStatus)}
            >
              {SIGN_OFF_CHOICES.map((choice) => (
                <option key={choice} value={choice}>
                  {choice === "pending" ? "Back to check" : ITEM_STATUS_LABELS[choice]}
                </option>
              ))}
            </AdminSelect>
            <TextInput
              aria-label="Link to the evidence"
              className="py-2 text-[14px]"
              value={evidence}
              maxLength={2000}
              placeholder={
                kind.evidence_required ? "Link to the evidence (needed)" : "Link to the evidence"
              }
              onChange={(event) => setEvidence(event.target.value)}
            />
          </div>
          <TextInput
            aria-label="Note"
            className="py-2 text-[14px]"
            value={note}
            maxLength={300}
            placeholder={status === "waived" ? "Why it is waived" : "Note (optional)"}
            onChange={(event) => setNote(event.target.value)}
          />
          {problem ? <p className="text-[12px] text-rag-red">{problem}</p> : null}
          <div className="flex justify-end gap-2">
            <Pill variant="ghost" size="sm" onClick={() => setEditing(false)}>
              Cancel
            </Pill>
            <Pill type="submit" size="sm" disabled={Boolean(problem) || save.isPending}>
              {save.isPending ? "Saving…" : "Save"}
            </Pill>
          </div>
        </form>
      ) : null}
    </li>
  );
}
