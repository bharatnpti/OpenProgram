import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { GateBoardResponse, QuestionStatus, TrackedQuestionResponse } from "../../api/schema";
import { useNames } from "../../app/directory";
import { useRole } from "../../app/role";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { cn } from "../../lib/utils";
import { actionError } from "../reports/access";
import { FormProblem, Locked, ReportDialog, field, fieldLabel } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import {
  QUESTION_LABELS,
  QUESTION_STATUSES,
  QUESTION_TONES,
  questionProblem,
  questionRows,
  questionSavedWords,
  questionsSummary,
  questionStatusSource,
} from "./gateWords";
import { useGateBoard } from "./queries";
import { VizCard } from "./viz";

type QuestionChange = { confirmed?: boolean; dismissed?: boolean; status?: QuestionStatus };

/**
 * Every question kept from Jira comments, or added by hand: what was asked, of
 * whom, and whether they answered. One read from Jira is listed once someone
 * keeps it; a status someone sets is not overwritten by the next read.
 */
export function QuestionsSection({
  projectId,
  releaseId = "",
}: {
  projectId: string;
  releaseId?: string;
}) {
  const { editGates, pastDay } = useReportAccess();
  const names = useNames();
  const queryClient = useQueryClient();
  const { query } = useGateBoard(projectId, releaseId || undefined);
  const board = query.data;
  const questions = board?.questions ?? [];
  const [adding, setAdding] = useState(false);
  const update = useMutation({
    mutationFn: ({ question, body }: { question: TrackedQuestionResponse; body: QuestionChange }) =>
      apiClient.updateQuestion(question.question_id, body),
    onSuccess: async (saved, { body }) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["gates", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["day-reports"] }),
      ]);
      toast.success(
        body.dismissed
          ? `Dismissed the question on ${saved.issue_key}.`
          : body.confirmed
            ? `Kept the question on ${saved.issue_key}.`
            : questionSavedWords(saved.issue_key, saved.status),
      );
    },
    onError: (error) => toast.error(actionError(error)),
  });
  const askedBy = (q: TrackedQuestionResponse) =>
    q.asked_by_name || board?.actor_names?.[q.asked_by] || names(q.asked_by);

  return (
    <section aria-labelledby="q-h">
      <SectionHeader
        id="q-h"
        title="What we asked, and what we heard back"
        meta={board ? questionsSummary(questions, editGates) : undefined}
        actions={
          editGates ? (
            board && board.issues.length > 0 ? (
              <Pill size="sm" variant="ghost" onClick={() => setAdding(true)}>
                Add a question
              </Pill>
            ) : undefined
          ) : pastDay("editGates") ? (
            <Locked>{pastDay("editGates")}</Locked>
          ) : undefined
        }
      />
      <PanelState
        isLoading={query.isLoading}
        error={query.error}
        isEmpty={questions.length === 0}
        emptyText="No questions kept yet. A Jira comment that mentions someone and asks, or starts with “Question:”, is suggested here; one asked elsewhere can be added by hand."
      >
        <VizCard className="@container">
          <ul className="m-0 grid list-none p-0" aria-label="Questions asked">
            {questionRows(questions).map((q) => (
              <li
                key={q.question_id}
                className="grid grid-cols-[minmax(0,1fr)] gap-x-3.5 gap-y-1.5 border-t border-(--op-viz-grid) py-3 first:border-t-0 first:pt-0 last:pb-0 @min-[720px]:grid-cols-[70px_minmax(0,1fr)_minmax(0,230px)] @min-[720px]:items-start"
              >
                <span className="text-[13px] font-extrabold">{q.issue_key}</span>
                <span className="min-w-0">
                  <span className="block text-[13.5px] font-bold text-ink">{q.summary}</span>
                  <span className="mt-0.5 block text-[12px] text-grey-secondary">
                    Asked of {q.asked_to_name || names(q.asked_to)} by {askedBy(q)} ·{" "}
                    {formatDay(q.asked_at)}
                    {!q.confirmed
                      ? editGates
                        ? " · read from Jira: keep it to track it"
                        : " · read from Jira, not kept, so not tracked"
                      : ""}
                  </span>
                </span>
                <span className="flex flex-wrap items-center gap-1.5">
                  {editGates && !q.confirmed ? (
                    <>
                      <Pill
                        size="sm"
                        variant="ghost"
                        className="h-8 px-3"
                        aria-label={`Keep the question on ${q.issue_key}`}
                        disabled={update.isPending}
                        onClick={() => update.mutate({ question: q, body: { confirmed: true } })}
                      >
                        Keep
                      </Pill>
                      <Pill
                        size="sm"
                        variant="ghost"
                        className="h-8 px-3"
                        aria-label={`Dismiss the question on ${q.issue_key}`}
                        disabled={update.isPending}
                        onClick={() => update.mutate({ question: q, body: { dismissed: true } })}
                      >
                        Dismiss
                      </Pill>
                    </>
                  ) : editGates ? (
                    <HeardBack
                      question={q}
                      askedTo={q.asked_to_name || names(q.asked_to)}
                      saving={update.isPending}
                      onSave={(status, done) =>
                        update.mutate({ question: q, body: { status } }, { onSuccess: done })
                      }
                    />
                  ) : q.confirmed ? (
                    <RagChip tone={QUESTION_TONES[q.status]} className="h-6 px-2.5 text-[12px]">
                      {QUESTION_LABELS[q.status]}
                    </RagChip>
                  ) : (
                    // Not kept, so nobody tracks whether it was heard back: "Not yet" would be a claim.
                    <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                      Not kept
                    </RagChip>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </VizCard>
      </PanelState>
      {adding && board ? (
        <AddQuestionDialog board={board} projectId={projectId} onClose={() => setAdding(false)} />
      ) : null}
    </section>
  );
}

/** A question asked outside Jira, on one of the scope's requirements. Mounted while open. */
function AddQuestionDialog({
  board,
  projectId,
  onClose,
}: {
  board: GateBoardResponse;
  projectId: string;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const { people } = useRole();
  const [issueKey, setIssueKey] = useState(board.issues[0]?.key ?? "");
  const [askedTo, setAskedTo] = useState("");
  const [summary, setSummary] = useState("");
  const [tried, setTried] = useState(false);
  const add = useMutation({
    mutationFn: () =>
      apiClient.addQuestion(issueKey, { asked_to: askedTo.trim(), summary: summary.trim() }),
    onSuccess: async (saved) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["gates", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["day-reports"] }),
      ]);
      toast.success(`Question added on ${saved.issue_key}.`);
      onClose();
    },
  });
  const problem = tried ? questionProblem(askedTo, summary) : null;

  return (
    <ReportDialog
      open
      onOpenChange={(next) => (next ? undefined : onClose())}
      title="Add a question"
      description="For a question asked outside Jira. It is tracked as not answered yet until someone says otherwise."
    >
      <form
        className="grid grid-cols-[minmax(0,1fr)] gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          setTried(true);
          if (!questionProblem(askedTo, summary)) add.mutate();
        }}
      >
        <div>
          <label htmlFor="question-issue" className={fieldLabel}>
            Ticket
          </label>
          <select
            id="question-issue"
            className={field}
            value={issueKey}
            onChange={(event) => setIssueKey(event.target.value)}
          >
            {board.issues.map((issue) => (
              <option key={issue.key} value={issue.key}>
                {issue.key} · {issue.title}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor="question-to" className={fieldLabel}>
            Asked of
          </label>
          <input
            id="question-to"
            className={field}
            list="question-people"
            value={askedTo}
            maxLength={200}
            placeholder="Who has to answer"
            onChange={(event) => setAskedTo(event.target.value)}
          />
          <datalist id="question-people">
            {people.map((person) => (
              <option key={person.id} value={person.name} />
            ))}
          </datalist>
        </div>
        <div>
          <label htmlFor="question-summary" className={fieldLabel}>
            What we asked
          </label>
          <textarea
            id="question-summary"
            className="min-h-24 w-full rounded-2xl border border-grey-border p-3 text-[14px]"
            value={summary}
            maxLength={1000}
            onChange={(event) => setSummary(event.target.value)}
          />
        </div>
        {problem ? <FormProblem>{problem}</FormProblem> : null}
        {add.error ? <FormProblem>{actionError(add.error)}</FormProblem> : null}
        <div className="flex justify-end gap-2">
          <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
            Cancel
          </Pill>
          <Pill type="submit" size="sm" disabled={add.isPending}>
            {add.isPending ? "Adding…" : "Add question"}
          </Pill>
        </div>
      </form>
    </ReportDialog>
  );
}

/**
 * What was heard back, chosen here and saved with its own button. A select
 * that saved on every change saved a status nobody chose: a closed select
 * changes with the arrow keys (Windows, Linux), one step per press, and a
 * person's status is never corrected by the next Jira read.
 *
 * Because the choice waits for Save, it says so: the select turns amber, "Not
 * saved yet" stands beside it with Save and Undo, and the row reads the same
 * after a reload only once it is saved. A chosen status left unsaved changed
 * nothing, silently, and the page looked as if it had.
 */
function HeardBack({
  question,
  askedTo,
  saving,
  onSave,
}: {
  question: TrackedQuestionResponse;
  askedTo: string;
  saving: boolean;
  onSave: (status: QuestionStatus, done: () => void) => void;
}) {
  const [choice, setChoice] = useState<QuestionStatus | null>(null);
  const value = choice ?? question.status;
  const changed = value !== question.status;
  const source = changed ? null : questionStatusSource(question, askedTo);
  return (
    <span className="grid gap-1">
      <span
        className={cn(
          "flex flex-wrap items-center gap-1.5",
          changed && "rounded-3xl bg-rag-amber-bg p-1.5",
        )}
      >
        {/* Named by aria-label: a visually hidden label is positioned outside
            the table's scroll box and widens a phone's page. */}
        <select
          aria-label={`Heard back on ${question.issue_key}?`}
          className={cn(
            "h-8 rounded-full border bg-(--op-viz-surface) px-3 text-[13px] font-bold",
            changed ? "border-rag-amber ring-2 ring-rag-amber" : "border-grey-border",
          )}
          value={value}
          onChange={(event) => setChoice(event.target.value as QuestionStatus)}
        >
          {QUESTION_STATUSES.map((status) => (
            <option key={status} value={status}>
              {QUESTION_LABELS[status]}
            </option>
          ))}
        </select>
        {changed ? (
          <>
            <span role="status" className="text-[12px] font-bold text-rag-amber-deep">
              Not saved yet
            </span>
            <Pill
              size="sm"
              className="h-8 px-3"
              aria-label={`Save what was heard back on ${question.issue_key}`}
              disabled={saving}
              onClick={() => onSave(value, () => setChoice(null))}
            >
              {saving ? "Saving…" : "Save"}
            </Pill>
            <Pill
              size="sm"
              variant="ghost"
              className="h-8 px-3"
              aria-label={`Undo the change on ${question.issue_key}`}
              disabled={saving}
              onClick={() => setChoice(null)}
            >
              Undo
            </Pill>
          </>
        ) : null}
      </span>
      {source ? (
        <span className="max-w-[260px] text-[11px] text-grey-secondary">{source}</span>
      ) : null}
    </span>
  );
}
