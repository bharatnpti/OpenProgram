import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CircleAlert, Plus, RefreshCw, X } from "lucide-react";
import { type ReactNode, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { GateBoardResponse, QuestionStatus, TrackedQuestionResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { useViewingDate } from "../../app/viewingDate";
import { Card } from "../../components/ui/Card";
import { TextArea, TextInput } from "../../components/ui/Field";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { cn } from "../../lib/utils";
import { AdminSelect } from "../admin/AdminSelect";
import { FormField } from "../admin/FormField";
import { errorMessage } from "../admin/adminTypes";
import { dayLabel } from "../forecast/forecast";
import { STAGE_COLORS, STAGE_LABELS } from "../requirements/stages";
import { IssueGatesDialog } from "./IssueGatesDialog";
import {
  GATE_STATE_TONES,
  QUESTION_STATUSES,
  QUESTION_STATUS_LABELS,
  QUESTION_STATUS_TONES,
  askedByName,
  boardKey,
  daysWaiting,
  evaluationFor,
  evaluationLabel,
  gateSummary,
  isOpen,
  issueRows,
  passedWithoutLine,
  questionRows,
  scanSummary,
  waitingLabel,
} from "./gates";

const LIST_LIMIT = 12;

/**
 * A project's requirements against their gates, and the questions asked on
 * them, for the whole project or one release.
 *
 * Items and questions are read from Jira as suggestions; they count once a
 * person confirms them, and only the roles a gate names sign its items off.
 */
export function ProjectGates({ projectId, asOf }: { projectId: string; asOf: string }) {
  const [releaseId, setReleaseId] = useState("");
  const releases = useQuery({
    queryKey: ["persona", "releases", projectId],
    queryFn: () => apiClient.releases(projectId),
  });
  const board = useQuery({
    queryKey: boardKey(projectId, asOf, releaseId),
    queryFn: () => apiClient.gateBoard(projectId, asOf, releaseId || undefined),
    placeholderData: (previous) => previous,
  });
  const releaseName = (releases.data ?? []).find((item) => item.release_id === releaseId)?.name;
  const scopeSwitch =
    (releases.data ?? []).length > 0 ? (
      <select
        aria-label="Which requirements"
        className="rounded-full border border-grey-border bg-white px-3 py-1.5 text-[13px] font-bold"
        value={releaseId}
        onChange={(event) => setReleaseId(event.target.value)}
      >
        <option value="">Whole project</option>
        {(releases.data ?? []).map((release) => (
          <option key={release.release_id} value={release.release_id}>
            {release.name}
          </option>
        ))}
      </select>
    ) : null;

  if (board.isError) {
    return (
      <Card padding="p-6">
        <h3 className="text-[18px] font-bold">Acceptance & tests</h3>
        <p className="mt-2 text-[14px] text-rag-red">{errorMessage(board.error)}</p>
      </Card>
    );
  }
  if (!board.data) {
    return (
      <Card padding="p-6">
        <h3 className="text-[18px] font-bold">Acceptance & tests</h3>
        <p className="mt-2 text-[14px] text-grey-secondary">Reading the gates…</p>
      </Card>
    );
  }
  return (
    <>
      <GatesCard
        board={board.data}
        projectId={projectId}
        asOf={asOf}
        releaseId={releaseId}
        scopeSwitch={scopeSwitch}
      />
      <QuestionsCard board={board.data} asOf={asOf} scopeName={releaseName} />
    </>
  );
}

function GatesCard({
  board,
  projectId,
  asOf,
  releaseId,
  scopeSwitch,
}: {
  board: GateBoardResponse;
  projectId: string;
  asOf: string;
  releaseId: string;
  scopeSwitch: ReactNode;
}) {
  const role = useRole();
  const { isPast } = useViewingDate();
  const canEditGates = role.canEditGates && !isPast;
  const queryClient = useQueryClient();
  const summary = gateSummary(board);
  const attention = issueRows(board, "attention");
  const [filter, setFilter] = useState<"attention" | "all" | null>(null);
  const shown = filter ?? (attention.length > 0 ? "attention" : "all");
  const rows = shown === "attention" ? attention : issueRows(board, "all");
  const [showAll, setShowAll] = useState(false);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const scan = useMutation({
    mutationFn: () => apiClient.scanGates(projectId, releaseId || undefined),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({ queryKey: ["persona", "gates", projectId] });
      toast.success(scanSummary(result));
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const pastGate = board.issues.filter((issue) => issue.passed_without.length > 0);

  return (
    <Card padding="p-6" className="flex flex-col gap-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-[18px] font-bold">Acceptance & tests</h3>
          <p className="mt-1 max-w-[620px] text-[13px] text-grey-secondary">
            What each requirement must pass before it moves on: the business's acceptance criteria
            and engineering's test cases. Read from Jira as suggestions; they count once someone
            confirms them.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          {scopeSwitch}
          {canEditGates ? (
            <Pill variant="ghost" size="sm" disabled={scan.isPending} onClick={() => scan.mutate()}>
              <RefreshCw size={14} className={cn(scan.isPending && "animate-spin")} />
              {scan.isPending ? "Reading Jira…" : "Read Jira now"}
            </Pill>
          ) : null}
        </div>
      </div>

      {board.templates.length === 0 ? (
        <p className="text-[14px] text-grey-secondary">
          No gate is switched on. An admin sets gates up under Configuration, Gates.
        </p>
      ) : board.issues.length === 0 ? (
        <p className="text-[14px] text-grey-secondary">No requirements in this scope yet.</p>
      ) : (
        <>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Tile label="Passed every gate" value={`${summary.passedAll} of ${summary.issues}`} />
            <Tile
              label="Moved on without its gate"
              value={String(summary.passedWithout)}
              alert={summary.passedWithout > 0}
            />
            <Tile label="Suggestions to confirm" value={String(summary.suggestions)} />
            <Tile
              label="Open questions"
              value={String(summary.openQuestions)}
              detail={
                summary.questionsToConfirm > 0
                  ? `${summary.questionsToConfirm} more read from Jira to keep`
                  : undefined
              }
            />
          </div>

          {pastGate.length > 0 ? (
            <div
              role="status"
              className="flex items-start gap-2 rounded-2xl bg-rag-red-bg px-4 py-3 text-[13px] text-rag-red"
            >
              <CircleAlert size={14} className="mt-0.5 shrink-0" />
              <ul className="flex flex-col gap-0.5">
                {pastGate.slice(0, 5).map((issue) => (
                  <li key={issue.key}>{passedWithoutLine(issue, STAGE_LABELS[issue.stage])}</li>
                ))}
                {pastGate.length > 5 ? <li>and {pastGate.length - 5} more.</li> : null}
              </ul>
            </div>
          ) : null}

          <div className="flex gap-2" role="group" aria-label="Which requirements to list">
            <FilterButton active={shown === "attention"} onClick={() => setFilter("attention")}>
              Needs someone ({attention.length})
            </FilterButton>
            <FilterButton active={shown === "all"} onClick={() => setFilter("all")}>
              All ({board.issues.length})
            </FilterButton>
          </div>

          {rows.length === 0 ? (
            <p className="text-[13px] text-grey-secondary">
              Nothing needs anyone: no suggestion waits and nothing moved on without its gate.
            </p>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-[14px]">
                <thead>
                  <tr className="border-b border-grey-border text-left text-[12px] uppercase tracking-wide text-grey-secondary">
                    <th className="py-2 pr-3 font-bold">Requirement</th>
                    <th className="px-3 py-2 font-bold">Stage</th>
                    {board.templates.map((template) => (
                      <th key={template.template_id} className="px-3 py-2 font-bold">
                        {template.name}
                        <span className="block text-[11px] font-normal normal-case tracking-normal">
                          before {STAGE_LABELS[template.guards_stage].toLowerCase()}
                        </span>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {(showAll ? rows : rows.slice(0, LIST_LIMIT)).map((issue) => (
                    <tr key={issue.key} className="border-b border-grey-border last:border-0">
                      <td className="py-2 pr-3">
                        <button
                          type="button"
                          className="text-left hover:underline"
                          onClick={() => setOpenKey(issue.key)}
                        >
                          <span className="font-bold">{issue.key}</span>{" "}
                          <span className="text-grey-body">{issue.title}</span>
                        </button>
                      </td>
                      <td className="px-3 py-2 whitespace-nowrap">
                        <span
                          aria-hidden
                          className="mr-2 inline-block h-2.5 w-2.5 rounded-full"
                          style={{ backgroundColor: STAGE_COLORS[issue.stage] }}
                        />
                        {STAGE_LABELS[issue.stage]}
                      </td>
                      {board.templates.map((template) => {
                        const evaluation = evaluationFor(issue, template.template_id);
                        const behind = issue.passed_without.includes(template.name);
                        return (
                          <td key={template.template_id} className="px-3 py-2">
                            {evaluation ? (
                              <RagChip
                                tone={behind ? "danger" : GATE_STATE_TONES[evaluation.state]}
                                className="h-6 whitespace-nowrap text-[12px]"
                              >
                                {evaluationLabel(evaluation)}
                              </RagChip>
                            ) : (
                              <span className="text-[13px] text-grey-secondary">Not needed</span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              {rows.length > LIST_LIMIT ? (
                <button
                  type="button"
                  className="mt-2 text-[13px] font-bold text-magenta hover:underline"
                  onClick={() => setShowAll(!showAll)}
                >
                  {showAll ? "Show fewer" : `Show all ${rows.length}`}
                </button>
              ) : null}
            </div>
          )}
        </>
      )}

      {openKey ? (
        <IssueGatesDialog
          board={board}
          issueKey={openKey}
          projectId={projectId}
          asOf={asOf}
          onClose={() => setOpenKey(null)}
        />
      ) : null}
    </Card>
  );
}

function QuestionsCard({
  board,
  asOf,
  scopeName,
}: {
  board: GateBoardResponse;
  asOf: string;
  scopeName?: string;
}) {
  const role = useRole();
  const { isPast } = useViewingDate();
  const canEditGates = role.canEditGates && !isPast;
  const queryClient = useQueryClient();
  const [showClosed, setShowClosed] = useState(false);
  const [adding, setAdding] = useState(false);
  const rows = questionRows(board.questions, showClosed);
  const closed = board.questions.filter(
    (question) => question.confirmed && !isOpen(question.status),
  ).length;
  const update = useMutation({
    mutationFn: ({
      question,
      body,
    }: {
      question: TrackedQuestionResponse;
      body: { confirmed?: boolean; dismissed?: boolean; status?: QuestionStatus };
    }) => apiClient.updateQuestion(question.question_id, body),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["persona", "gates", board.project_id] });
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  return (
    <Card padding="p-6" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-[18px] font-bold">Questions we asked</h3>
          <p className="mt-1 max-w-[620px] text-[13px] text-grey-secondary">
            Asked in Jira comments by mentioning someone{scopeName ? `, on ${scopeName}` : ""}, and
            whether they have answered. A question read from Jira is listed once someone keeps it.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          {closed > 0 ? (
            <label className="flex items-center gap-2 text-[13px] font-bold">
              <input
                type="checkbox"
                checked={showClosed}
                onChange={(event) => setShowClosed(event.target.checked)}
              />
              Show answered ({closed})
            </label>
          ) : null}
          {canEditGates && board.issues.length > 0 ? (
            <Pill variant="ghost" size="sm" onClick={() => setAdding(true)}>
              <Plus size={14} />
              Add question
            </Pill>
          ) : null}
        </div>
      </div>

      {rows.length === 0 ? (
        <p className="text-[13px] text-grey-secondary">
          {board.questions.length === 0
            ? "No question found in Jira comments yet."
            : "Every question has been answered."}
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-[14px]">
            <thead>
              <tr className="border-b border-grey-border text-left text-[12px] uppercase tracking-wide text-grey-secondary">
                <th className="py-2 pr-3 font-bold">Ticket</th>
                <th className="px-3 py-2 font-bold">What we asked</th>
                <th className="px-3 py-2 font-bold">Asked to</th>
                <th className="px-3 py-2 font-bold">Asked on</th>
                <th className="py-2 pl-3 font-bold">Heard back?</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((question) => {
                const waited = daysWaiting(question.asked_at, asOf);
                return (
                  <tr
                    key={question.question_id}
                    className={cn(
                      "border-b border-grey-border align-top last:border-0",
                      !question.confirmed && "bg-grey-fill/60",
                    )}
                  >
                    <td className="py-2.5 pr-3 font-bold whitespace-nowrap">
                      {question.issue_key}
                    </td>
                    <td className="px-3 py-2.5">
                      {question.summary}
                      <span className="block text-[12px] text-grey-secondary">
                        {question.confirmed ? "Asked" : "Read from Jira, asked"} by{" "}
                        {askedByName(question, board.actor_names ?? {})}
                      </span>
                    </td>
                    <td className="px-3 py-2.5">
                      {question.asked_to_name || question.asked_to || "—"}
                    </td>
                    <td className="px-3 py-2.5 whitespace-nowrap">
                      {dayLabel(question.asked_at.slice(0, 10))}
                      {isOpen(question.status) ? (
                        <span
                          className={cn(
                            "block text-[12px]",
                            waited >= 5 ? "font-bold text-rag-amber" : "text-grey-secondary",
                          )}
                        >
                          waiting {waitingLabel(waited)}
                        </span>
                      ) : null}
                    </td>
                    <td className="py-2 pl-3">
                      {!question.confirmed && canEditGates ? (
                        <div className="flex flex-wrap items-center gap-1">
                          <RagChip
                            tone={QUESTION_STATUS_TONES[question.status]}
                            className="mr-1 h-6 text-[12px]"
                          >
                            {QUESTION_STATUS_LABELS[question.status]}
                          </RagChip>
                          <Pill
                            size="sm"
                            variant="ghost"
                            className="h-8 px-3"
                            disabled={update.isPending}
                            onClick={() => update.mutate({ question, body: { confirmed: true } })}
                          >
                            <Check size={13} />
                            Keep
                          </Pill>
                          <button
                            type="button"
                            aria-label={`Dismiss the question on ${question.issue_key}`}
                            className="rounded-full p-2 text-grey-secondary hover:bg-grey-fill hover:text-ink"
                            disabled={update.isPending}
                            onClick={() => update.mutate({ question, body: { dismissed: true } })}
                          >
                            <X size={14} />
                          </button>
                        </div>
                      ) : canEditGates ? (
                        <AdminSelect
                          aria-label={`Answered? ${question.issue_key}`}
                          className="py-1.5 text-[13px]"
                          value={question.status}
                          disabled={update.isPending}
                          onChange={(event) =>
                            update.mutate({
                              question,
                              body: { status: event.target.value as QuestionStatus },
                            })
                          }
                        >
                          {QUESTION_STATUSES.map((status) => (
                            <option key={status} value={status}>
                              {QUESTION_STATUS_LABELS[status]}
                            </option>
                          ))}
                        </AdminSelect>
                      ) : (
                        <RagChip
                          tone={QUESTION_STATUS_TONES[question.status]}
                          className="h-6 text-[12px]"
                        >
                          {QUESTION_STATUS_LABELS[question.status]}
                        </RagChip>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {adding ? <AddQuestionDialog board={board} onClose={() => setAdding(false)} /> : null}
    </Card>
  );
}

function AddQuestionDialog({ board, onClose }: { board: GateBoardResponse; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [issueKey, setIssueKey] = useState(board.issues[0]?.key ?? "");
  const [askedTo, setAskedTo] = useState("");
  const [summary, setSummary] = useState("");
  const add = useMutation({
    mutationFn: () =>
      apiClient.addQuestion(issueKey, { asked_to: askedTo.trim(), summary: summary.trim() }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["persona", "gates", board.project_id] });
      toast.success("Question added.");
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const ready = Boolean(issueKey && askedTo.trim() && summary.trim());
  return (
    <Modal open onOpenChange={(open) => (open ? undefined : onClose())} title="Add a question">
      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          if (ready) add.mutate();
        }}
      >
        <FormField label="Ticket" htmlFor="question-issue">
          <AdminSelect
            id="question-issue"
            value={issueKey}
            onChange={(event) => setIssueKey(event.target.value)}
          >
            {board.issues.map((issue) => (
              <option key={issue.key} value={issue.key}>
                {issue.key} · {issue.title}
              </option>
            ))}
          </AdminSelect>
        </FormField>
        <FormField label="Asked to" htmlFor="question-to">
          <TextInput
            id="question-to"
            value={askedTo}
            maxLength={200}
            placeholder="Who has to answer"
            onChange={(event) => setAskedTo(event.target.value)}
          />
        </FormField>
        <FormField label="What we asked" htmlFor="question-summary">
          <TextArea
            id="question-summary"
            value={summary}
            maxLength={1000}
            onChange={(event) => setSummary(event.target.value)}
          />
        </FormField>
        <div className="flex justify-end gap-2">
          <Pill variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={!ready || add.isPending}>
            {add.isPending ? "Adding…" : "Add question"}
          </Pill>
        </div>
      </form>
    </Modal>
  );
}

function Tile({
  label,
  value,
  detail,
  alert = false,
}: {
  label: string;
  value: string;
  detail?: string;
  alert?: boolean;
}) {
  return (
    <div className={cn("rounded-2xl px-4 py-3", alert ? "bg-rag-red-bg" : "bg-grey-fill")}>
      <div className="text-[12px] font-bold uppercase tracking-wide text-grey-secondary">
        {label}
      </div>
      <div className={cn("mt-1 text-[20px] font-extrabold", alert && "text-rag-red")}>{value}</div>
      {detail ? <div className="text-[12px] text-grey-secondary">{detail}</div> : null}
    </div>
  );
}

function FilterButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      className={cn(
        "rounded-full px-3 py-1.5 text-[13px] font-bold transition-colors",
        active ? "bg-ink text-white" : "border border-grey-border bg-white hover:bg-grey-fill",
      )}
      onClick={onClick}
    >
      {children}
    </button>
  );
}
