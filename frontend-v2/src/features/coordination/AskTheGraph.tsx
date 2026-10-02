import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { useViewingDate } from "../../app/viewingDate";
import { Card } from "../../components/ui/Card";
import { TextArea } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import { formatDayLabel } from "../../lib/viewingDate";

export function AskTheGraph() {
  const [question, setQuestion] = useState("");
  const { canReadAggregate } = useRole();
  const { asOf, isPast, label, today } = useViewingDate();

  // The day travels with each question, so an answer keeps the date it was
  // asked for after the viewing date moves on.
  const ask = useMutation({
    mutationFn: (day: string) => apiClient.ask({ question, as_of: day }),
    onError: (error) => toast.error(error instanceof Error ? error.message : "Ask failed"),
  });
  const answeredFor = ask.variables;

  // /ask answers across the graph, so it needs an aggregate read. A developer
  // gets the explanation instead of a box that rejects every question.
  if (!canReadAggregate) {
    return (
      <Card padding="p-6" className="lg:sticky lg:top-24">
        <h2 className="text-[18px] font-bold">Ask the graph</h2>
        <p className="mt-2 text-sm text-grey-secondary">
          Questions are answered across the delivery graph, which needs a team or executive role.
        </p>
      </Card>
    );
  }

  return (
    <Card padding="p-6" className="lg:sticky lg:top-24">
      <h2 className="text-[18px] font-bold">Ask the graph</h2>
      {isPast ? (
        <p className="mt-1 text-[13px] text-grey-secondary">Answers are as of {label}.</p>
      ) : null}
      <TextArea
        className="mt-3"
        placeholder="e.g. Which workstreams are at risk this week?"
        value={question}
        onChange={(event) => setQuestion(event.target.value)}
      />
      <Pill
        variant="dark"
        size="md"
        className="mt-3 w-full"
        disabled={ask.isPending || !question.trim()}
        onClick={() => ask.mutate(asOf)}
      >
        {ask.isPending ? "Thinking…" : "Ask"}
      </Pill>

      {ask.data ? (
        <div className="animate-op-pop mt-4 rounded-2xl bg-grey-fill p-4">
          {answeredFor && answeredFor !== today ? (
            <div className="mb-2 text-xs font-bold uppercase tracking-wide text-grey-secondary">
              As of {formatDayLabel(answeredFor, today)}
            </div>
          ) : null}
          <p className="text-[14px]">{ask.data.answer}</p>
          {ask.data.references.length > 0 ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {ask.data.references.map((reference) => (
                <span
                  key={reference}
                  className="rounded-full bg-white px-3 py-1 text-[12px] font-bold"
                >
                  {reference}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
