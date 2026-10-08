import { useMutation, useQueryClient } from "@tanstack/react-query";
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
import { useNames } from "../../app/directory";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { STAGE_LABELS } from "../../lib/status";
import { actionError, maySignOff, signOffWho } from "../reports/access";
import { FormProblem, Locked, ReportDialog, field, fieldLabel } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import {
  GATE_STATE_TONES,
  ITEM_STATUS_LABELS,
  ITEM_STATUS_TONES,
  SIGN_OFF_CHOICES,
  SOURCE_LABELS,
  evaluationChip,
  evaluationLine,
  evidenceProblem,
  itemsByKind,
  withArticle,
} from "./gateWords";

/** Every read a gate change moves: the board and today's report. */
function useGateRefresh(projectId: string) {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["gates", projectId] }),
      queryClient.invalidateQueries({ queryKey: ["day-reports"] }),
    ]);
}

/**
 * One requirement against each gate that applies to it: suggestions read from
 * Jira to keep or dismiss, kept items to sign off, and a way to add one by
 * hand. Only the roles a gate names for a kind sign that kind off.
 */
export function IssueGatesDialog({
  board,
  issueKey,
  projectId,
  onClose,
}: {
  board: GateBoardResponse;
  issueKey: string;
  projectId: string;
  onClose: () => void;
}) {
  const { editGates, pastDay } = useReportAccess();
  const issue = board.issues.find((item) => item.key === issueKey);
  if (!issue) return null;
  const templates = board.templates.filter((template) =>
    issue.evaluations.some((evaluation) => evaluation.template_id === template.template_id),
  );

  return (
    <ReportDialog
      open
      wide
      onOpenChange={(next) => (next ? undefined : onClose())}
      title={`${issue.key} ${issue.title}`}
      description={`${STAGE_LABELS[issue.stage]}${issue.status ? ` · Jira status ${issue.status}` : ""}. An item read from Jira counts once someone keeps it.`}
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        {issue.passed_without.length > 0 ? (
          <p className="rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] font-bold text-rag-red">
            Reached {STAGE_LABELS[issue.stage].toLowerCase()} without{" "}
            {issue.passed_without.join(" and ")} passing.
          </p>
        ) : null}
        {!editGates && pastDay("editGates") ? <Locked>{pastDay("editGates")}</Locked> : null}
        {templates.length === 0 ? (
          <p className="text-[14px] text-grey-secondary">No gate applies to this issue type.</p>
        ) : (
          templates.map((template) => (
            <TemplateBlock
              key={template.template_id}
              issue={issue}
              template={template}
              projectId={projectId}
              actorNames={board.actor_names ?? {}}
            />
          ))
        )}
        <div className="flex justify-end">
          <Pill variant="ghost" size="sm" onClick={onClose}>
            Close
          </Pill>
        </div>
      </div>
    </ReportDialog>
  );
}

function TemplateBlock({
  issue,
  template,
  projectId,
  actorNames,
}: {
  issue: IssueGatesResponse;
  template: GateTemplateDto;
  projectId: string;
  actorNames: Record<string, string>;
}) {
  const evaluation = issue.evaluations.find((e) => e.template_id === template.template_id);
  return (
    <section className="grid grid-cols-[minmax(0,1fr)] gap-4 rounded-2xl border border-grey-border p-4">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h3 className="text-[16px] font-extrabold">{template.name}</h3>
          <p className="text-[12px] text-grey-secondary">
            Passed before {STAGE_LABELS[template.guards_stage].toLowerCase()}
          </p>
        </div>
        {evaluation ? (
          <div className="grid justify-items-end gap-1">
            <RagChip tone={GATE_STATE_TONES[evaluation.state]} className="h-6 px-2.5 text-[12px]">
              {evaluationChip(evaluation)}
            </RagChip>
            <span className="text-[12px] text-grey-body">
              {evaluationLine(evaluation, template)}
            </span>
          </div>
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
          actorNames={actorNames}
        />
      ))}
    </section>
  );
}

function KindBlock({
  issueKey,
  templateId,
  kind,
  suggested,
  confirmed,
  projectId,
  actorNames,
}: {
  issueKey: string;
  templateId: string;
  kind: ItemKindDto;
  suggested: GateItemResponse[];
  confirmed: GateItemResponse[];
  projectId: string;
  actorNames: Record<string, string>;
}) {
  const { editGates, lens } = useReportAccess();
  const refresh = useGateRefresh(projectId);
  const [text, setText] = useState("");
  const decide = useMutation({
    mutationFn: ({ item, keep }: { item: GateItemResponse; keep: boolean }) =>
      keep ? apiClient.confirmGateItem(item.item_id) : apiClient.dismissGateItem(item.item_id),
    onSuccess: async (_item, { keep }) => {
      await refresh();
      toast.success(
        keep ? "Kept: it now counts for the gate." : "Dismissed: it is not suggested again.",
      );
    },
    onError: (error) => toast.error(actionError(error)),
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
      await refresh();
      toast.success(`${kind.label} added. It is to check until someone signs it off.`);
    },
  });
  const signs = editGates && maySignOff(kind, lens);

  return (
    <div role="group" aria-label={kind.label} className="grid grid-cols-[minmax(0,1fr)] gap-2">
      <div>
        <h4 className="text-[14px] font-extrabold">{kind.label}</h4>
        <p className="text-[12px] text-grey-secondary">
          Signed off by {signOffWho(kind)}
          {kind.evidence_required ? ", met only with a link to the evidence" : ""}.
        </p>
      </div>

      {suggested.length > 0 ? (
        <div className="rounded-2xl bg-grey-fill px-3 py-2">
          <p className="text-[12px] font-bold text-grey-secondary">
            Read from Jira. Keep the ones that belong.
          </p>
          <ul className="mt-1 grid gap-2">
            {suggested.map((item) => (
              <li key={item.item_id} className="flex flex-wrap items-start justify-between gap-2">
                <span className="min-w-0 text-[14px]">
                  {item.text}
                  <span className="block text-[12px] text-grey-secondary">
                    {SOURCE_LABELS[item.source]}
                  </span>
                </span>
                {editGates ? (
                  <span className="flex flex-none gap-1.5">
                    <Pill
                      size="sm"
                      variant="ghost"
                      className="h-8 px-3"
                      aria-label={`Keep: ${item.text}`}
                      disabled={decide.isPending}
                      onClick={() => decide.mutate({ item, keep: true })}
                    >
                      Keep
                    </Pill>
                    <Pill
                      size="sm"
                      variant="ghost"
                      className="h-8 px-3"
                      aria-label={`Dismiss: ${item.text}`}
                      disabled={decide.isPending}
                      onClick={() => decide.mutate({ item, keep: false })}
                    >
                      Dismiss
                    </Pill>
                  </span>
                ) : null}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {confirmed.length === 0 ? (
        <p className="text-[13px] text-grey-secondary">No {kind.label.toLowerCase()} kept yet.</p>
      ) : (
        <ul className="grid grid-cols-[minmax(0,1fr)] divide-y divide-grey-border">
          {confirmed.map((item) => (
            <ItemRow
              key={item.item_id}
              item={item}
              kind={kind}
              signs={signs}
              projectId={projectId}
              actorNames={actorNames}
            />
          ))}
        </ul>
      )}

      {editGates ? (
        <form
          className="grid grid-cols-[minmax(0,1fr)_auto] gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            if (text.trim()) add.mutate();
          }}
        >
          <input
            aria-label={`Add ${withArticle(kind.label)}`}
            className={field}
            value={text}
            maxLength={1000}
            placeholder={`Add ${withArticle(kind.label)}`}
            onChange={(event) => setText(event.target.value)}
          />
          <Pill type="submit" variant="ghost" size="sm" disabled={!text.trim() || add.isPending}>
            {add.isPending ? "Adding…" : "Add"}
          </Pill>
          {add.error ? (
            <div className="col-span-2">
              <FormProblem>{actionError(add.error)}</FormProblem>
            </div>
          ) : null}
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
  actorNames,
}: {
  item: GateItemResponse;
  kind: ItemKindDto;
  signs: boolean;
  projectId: string;
  actorNames: Record<string, string>;
}) {
  const names = useNames();
  const refresh = useGateRefresh(projectId);
  const [open, setOpen] = useState(false);
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
    onSuccess: async (saved) => {
      setOpen(false);
      await refresh();
      toast.success(
        saved.status === "pending"
          ? "Back to check."
          : `Signed off as ${ITEM_STATUS_LABELS[saved.status].toLowerCase()}.`,
      );
    },
  });
  const problem = evidenceProblem(evidence, kind.evidence_required ?? false, status);
  const signer = item.signed_by ? (actorNames[item.signed_by] ?? names(item.signed_by)) : null;

  return (
    <li className="grid grid-cols-[minmax(0,1fr)] gap-2 py-2.5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <span className="min-w-0 text-[14px]">
          {item.text}
          <span className="block text-[12px] text-grey-secondary">
            {signer && item.signed_at && item.status !== "pending"
              ? `${ITEM_STATUS_LABELS[item.status]} by ${signer}, ${formatDay(item.signed_at)}`
              : SOURCE_LABELS[item.source]}
            {item.note ? ` · ${item.note}` : ""}
          </span>
          {item.evidence_url ? (
            <a
              href={item.evidence_url}
              target="_blank"
              rel="noreferrer"
              className="text-[12px] font-bold"
            >
              Evidence
            </a>
          ) : null}
        </span>
        <span className="flex flex-none items-center gap-2">
          <RagChip tone={ITEM_STATUS_TONES[item.status]} className="h-6 px-2.5 text-[12px]">
            {ITEM_STATUS_LABELS[item.status]}
          </RagChip>
          {signs && !open ? (
            <Pill
              size="sm"
              variant="ghost"
              className="h-8 px-3"
              aria-label={`Sign off: ${item.text}`}
              onClick={() => setOpen(true)}
            >
              Sign off
            </Pill>
          ) : null}
        </span>
      </div>
      {open ? (
        <form
          className="grid grid-cols-[minmax(0,1fr)] gap-3 rounded-2xl bg-grey-fill p-3"
          onSubmit={(event) => {
            event.preventDefault();
            if (!problem) save.mutate();
          }}
        >
          <div className="grid grid-cols-[minmax(0,1fr)] gap-3 sm:grid-cols-[180px_minmax(0,1fr)]">
            <div>
              <label htmlFor={`status-${item.item_id}`} className={fieldLabel}>
                Sign off as
              </label>
              <select
                id={`status-${item.item_id}`}
                className={field}
                value={status}
                onChange={(event) => setStatus(event.target.value as ItemStatus)}
              >
                {SIGN_OFF_CHOICES.map((choice) => (
                  <option key={choice} value={choice}>
                    {choice === "pending" ? "Back to check" : ITEM_STATUS_LABELS[choice]}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label htmlFor={`evidence-${item.item_id}`} className={fieldLabel}>
                Evidence link{kind.evidence_required ? " (needed to mark it met)" : " (optional)"}
              </label>
              <input
                id={`evidence-${item.item_id}`}
                className={field}
                value={evidence}
                maxLength={2000}
                placeholder="https://"
                onChange={(event) => setEvidence(event.target.value)}
              />
            </div>
          </div>
          <div>
            <label htmlFor={`note-${item.item_id}`} className={fieldLabel}>
              {status === "waived" ? "Why it is waived" : "Note (optional)"}
            </label>
            <input
              id={`note-${item.item_id}`}
              className={field}
              value={note}
              maxLength={300}
              onChange={(event) => setNote(event.target.value)}
            />
          </div>
          {problem ? <FormProblem>{problem}</FormProblem> : null}
          {save.error ? <FormProblem>{actionError(save.error)}</FormProblem> : null}
          <div className="flex justify-end gap-2">
            <Pill type="button" variant="ghost" size="sm" onClick={() => setOpen(false)}>
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
