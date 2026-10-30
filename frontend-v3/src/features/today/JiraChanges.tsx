import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { JiraChangeAnswerResponse, JiraChangeDto } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { actionError } from "../../lib/errors";
import { cn } from "../../lib/utils";
import { VizCard } from "../overall/viz";
import { useMyJiraChanges } from "./queries";
import { answerButtons, changeTitle, dueDateWords, gateWords } from "./todayWords";

/**
 * "Jira changes waiting for your yes": the person's own, never anyone else's.
 * Each row says what a yes does and why it is asked; Yes appears only where
 * every gate of the write would let it through, No for any change the console
 * can answer, and a due date only says to change it in Jira. The answer goes
 * through the same path as a yes or no in the DM: the issue moves once
 * whichever is first, a check-in held for the answer is recorded, and the DM
 * gets one line saying it was answered here. Drawn only while something waits.
 */
export function JiraChanges() {
  const changes = useMyJiraChanges();
  const items = changes.data?.items ?? [];
  const tracker = changes.data?.tracker ?? "Jira";
  // An answer's outcome stays on its row until the list is read again without it.
  const [answered, setAnswered] = useState<Record<string, JiraChangeAnswerResponse>>({});
  const outcomes = Object.values(answered).filter(
    (answer) => !items.some((item) => item.id === answer.item_id),
  );
  if (!changes.isError && items.length === 0 && outcomes.length === 0) return null;

  return (
    <VizCard aria-label="Jira changes waiting for your yes" className="gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[17px] font-extrabold">Jira changes waiting for your yes</h2>
        <span className="text-[12px] text-grey-secondary">Ora never changes Jira on its own.</span>
      </div>
      <PanelState
        isLoading={changes.isLoading}
        error={changes.error}
        onRetry={() => void changes.refetch()}
      >
        <ul className="grid grid-cols-[minmax(0,1fr)]">
          {items.map((item) => (
            <ChangeRow
              key={item.id}
              item={item}
              tracker={tracker}
              outcome={answered[item.id]}
              onAnswered={(answer) => setAnswered((all) => ({ ...all, [item.id]: answer }))}
            />
          ))}
          {outcomes.map((answer) => (
            <li
              key={answer.item_id}
              className="border-t border-grey-border py-3 first:border-t-0 text-[14px]"
            >
              <p className="font-bold text-ink">{answer.issue_key}</p>
              <Outcome answer={answer} />
            </li>
          ))}
        </ul>
      </PanelState>
    </VizCard>
  );
}

function ChangeRow({
  item,
  tracker,
  outcome,
  onAnswered,
}: {
  item: JiraChangeDto;
  tracker: string;
  outcome: JiraChangeAnswerResponse | undefined;
  onAnswered: (answer: JiraChangeAnswerResponse) => void;
}) {
  const queryClient = useQueryClient();
  const buttons = answerButtons(item);
  const blocked = gateWords(item.gate, item, tracker);
  const answer = useMutation({
    mutationFn: (choice: "yes" | "no") => apiClient.answerMyJiraChange(item.id, choice),
    onSuccess: (result) => {
      onAnswered(result);
      toast.success(result.detail);
      // The check-in it held may now be recorded, and the issue has moved.
      void queryClient.invalidateQueries({ queryKey: ["me"] });
    },
    onError: (error: unknown) => toast.error(actionError(error, "answer this change")),
  });

  return (
    <li className="flex min-w-0 flex-wrap items-start gap-x-4 gap-y-2 border-t border-grey-border py-3 first:border-t-0">
      <div className="min-w-0 flex-[1_1_260px]">
        <p className="text-[14px] font-bold text-ink">
          {changeTitle(item)}
          {item.issue_title ? (
            <span className="font-medium text-grey-body"> · {item.issue_title}</span>
          ) : null}
        </p>
        <p className="mt-0.5 text-[12.5px] text-grey-secondary">{item.why}</p>
        {item.kind === "due_date" ? (
          <p className="mt-1 text-[12.5px] font-bold text-grey-body">{dueDateWords(tracker)}</p>
        ) : null}
        {blocked ? <p className="mt-1 text-[12.5px] text-rag-amber-deep">{blocked}</p> : null}
        {item.holds_checkin ? (
          <p className="mt-1 text-[12px] text-grey-secondary">
            Your check-in is recorded once this is answered.
          </p>
        ) : null}
        {outcome ? <Outcome answer={outcome} /> : null}
      </div>
      {!outcome && (buttons.yes || buttons.no) ? (
        <div className="flex flex-none flex-wrap gap-2">
          {buttons.yes ? (
            <Pill
              size="sm"
              disabled={answer.isPending}
              onClick={() => answer.mutate("yes")}
              aria-label={`Yes, update ${item.issue_key} in ${tracker}`}
            >
              Yes, update {tracker}
            </Pill>
          ) : null}
          {buttons.no ? (
            <Pill
              size="sm"
              variant="ghost"
              disabled={answer.isPending}
              onClick={() => answer.mutate("no")}
              aria-label={`No, leave ${item.issue_key} as it is`}
            >
              No
            </Pill>
          ) : null}
        </div>
      ) : null}
    </li>
  );
}

function Outcome({ answer }: { answer: JiraChangeAnswerResponse }) {
  const good = answer.outcome === "applied" || answer.outcome === "declined";
  return (
    <p
      role="status"
      className={cn(
        "mt-1 text-[12.5px] font-bold",
        good ? "text-rag-green" : "text-rag-amber-deep",
      )}
    >
      {answer.detail}
      {answer.checkin_finished ? " Your check-in is recorded." : ""}
      {answer.line_sent ? " Your chat with Ora says so too." : ""}
    </p>
  );
}
