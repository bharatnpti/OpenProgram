import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { ConversationTurnDto } from "../../api/schema";
import { PanelState } from "../../components/PanelState";
import { formatDay, formatTime } from "../../lib/format";
import { cn } from "../../lib/utils";
import { VizCard } from "../overall/viz";
import { emptyConversationWords, orderedTurns, turnAuthor } from "./conversationWords";

/**
 * The person's own conversation with Ora on one day, read-only and
 * styled like the chat: Ora's messages on the left, theirs on the right. It is
 * only ever their own (`GET /me/conversation` takes no person id, so an admin
 * sees theirs here too), and the read never refreshes how long the messages
 * are kept. There is no reply box: replying is done in the chat, or by saving
 * the form beside it, which says so itself.
 */
export function Conversation({ day, isToday }: { day: string; isToday: boolean }) {
  const conversation = useQuery({
    queryKey: ["me", "conversation", day],
    queryFn: () => apiClient.myConversation(day),
    enabled: day !== "",
  });
  const data = conversation.data;
  const turns = orderedTurns(data?.turns ?? []);

  return (
    <VizCard aria-label="Your conversation with Ora" className="content-start gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="text-[17px] font-extrabold">Your conversation with Ora</h2>
        <span className="text-[12px] text-grey-secondary">
          {isToday ? "Today" : formatDay(day)} · only you see this
        </span>
      </div>
      <PanelState
        isLoading={conversation.isLoading || day === ""}
        error={conversation.error}
        onRetry={() => void conversation.refetch()}
        isEmpty={turns.length === 0}
        emptyText={data ? emptyConversationWords(data, isToday) : undefined}
      >
        <ol className="grid gap-3" aria-label="Messages, oldest first">
          {turns.map((turn, index) => (
            <li key={`${turn.at}-${index}`}>
              <Bubble turn={turn} />
            </li>
          ))}
        </ol>
      </PanelState>
      <p className="border-t border-grey-border pt-3 text-[12px] text-grey-secondary">
        Reply to Ora in your chat.
      </p>
    </VizCard>
  );
}

function Bubble({ turn }: { turn: ConversationTurnDto }) {
  const ora = turn.role !== "user";
  return (
    <div className={cn("flex", ora ? "justify-start" : "justify-end")}>
      <div
        className={cn(
          "max-w-[85%] rounded-2xl px-4 py-2.5 text-[14px] text-ink",
          ora ? "bg-grey-fill" : "bg-rag-info-bg",
        )}
      >
        <p className="whitespace-pre-line break-words">{turn.text}</p>
        <p className="mt-1 text-[11px] text-grey-secondary">
          {turnAuthor(turn)} · {formatTime(turn.at)}
        </p>
      </div>
    </div>
  );
}
