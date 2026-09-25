import { useMutation } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { Card } from "../../components/ui/Card";
import { TextArea } from "../../components/ui/Field";
import { Pill } from "../../components/ui/Pill";
import { todayIso } from "../../lib/today";

export function AskTheGraph() {
  const [question, setQuestion] = useState("");
  const { canReadAggregate } = useRole();

  const ask = useMutation({
    mutationFn: () => apiClient.ask({ question, as_of: todayIso() }),
    onError: (error) => toast.error(error instanceof Error ? error.message : "Ask failed"),
  });

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
        onClick={() => ask.mutate()}
      >
        {ask.isPending ? "Thinking…" : "Ask"}
      </Pill>

      {ask.data ? (
        <div className="animate-op-pop mt-4 rounded-2xl bg-grey-fill p-4">
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
