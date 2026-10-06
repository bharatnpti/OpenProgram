import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../api/client";
import type { ChatSimulatorMessageResponse } from "../api/schema";
import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { Pill } from "../components/ui/Pill";
import { formatDay, formatTime } from "../lib/format";
import { cn } from "../lib/utils";

/** The purpose the backend gives a DM that asks a person for a review or input. */
const REQUEST_PURPOSE = "cross_person_request";

const PURPOSE_WORDS: Record<string, string> = {
  checkin: "check-in",
  followup: "follow-up",
  nudge: "nudge",
  cross_person_request: "request",
};

/**
 * The built-in chat that stands in for Slack on a local tenant: one thread per
 * person, the bot's questions, and a composer whose reply travels the same
 * path as a real chat reply, so it lands in the rollups. Everyone reads and
 * answers their own thread; an admin gets the roster, can ask anyone for a
 * check-in, and can clear the history.
 */
export function ChatPage() {
  const { chatEnabled, canManageConfig, people, actingAs } = useRole();
  const queryClient = useQueryClient();
  const [chosen, setChosen] = useState("");
  const threadId = canManageConfig && chosen ? chosen : (actingAs?.id ?? "");
  const person = people.find((p) => p.id === threadId);

  const status = useQuery({
    queryKey: ["chat", "status"],
    queryFn: () => apiClient.chatSimulatorStatus(),
    enabled: chatEnabled,
  });
  const messages = useQuery({
    queryKey: ["chat", "messages", threadId],
    queryFn: () => apiClient.chatSimulatorMessages(threadId),
    enabled: chatEnabled && Boolean(threadId),
    refetchInterval: 5000,
  });

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: ["chat"] }),
      queryClient.invalidateQueries({ queryKey: ["me"] }),
      queryClient.invalidateQueries({ queryKey: ["pod"] }),
      queryClient.invalidateQueries({ queryKey: ["requests"] }),
    ]);

  const requestCheckin = useMutation({
    mutationFn: () =>
      apiClient.dispatchCheckin({
        tenant_id: status.data?.tenant_id ?? "demo",
        developer_id: threadId,
        developer_name: person?.name ?? threadId,
        chat_external_id: threadId,
      }),
    onSuccess: async () => {
      await refresh();
      toast.success("Check-in requested. The bot asks in a moment.");
    },
    onError: (e: Error) => toast.error(e.message),
  });
  const [confirmClear, setConfirmClear] = useState(false);
  const clear = useMutation({
    mutationFn: () => apiClient.resetChatSimulatorState(),
    onSuccess: async () => {
      setConfirmClear(false);
      await refresh();
      toast.success("Chat history cleared for everyone.");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (!chatEnabled) {
    return (
      <>
        <SectionHeader title="Chat" />
        <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
          This tenant does not serve the built-in chat. Check-ins arrive in your chat workspace
          instead.
        </p>
      </>
    );
  }

  return (
    <>
      <SectionHeader
        title="Chat"
        meta="The check-in conversation. Replies are parsed into status the same way a chat reply is."
        actions={
          canManageConfig && threadId ? (
            <>
              <Pill
                size="sm"
                variant="ghost"
                disabled={requestCheckin.isPending}
                onClick={() => requestCheckin.mutate()}
              >
                Ask {person?.name.split(" ")[0] ?? "them"} for a check-in
              </Pill>
              {confirmClear ? (
                <span className="flex items-center gap-2 text-[13px]">
                  Clear every thread?
                  <Pill size="sm" variant="dark" onClick={() => clear.mutate()}>
                    Clear
                  </Pill>
                  <Pill size="sm" variant="ghost" onClick={() => setConfirmClear(false)}>
                    Keep
                  </Pill>
                </span>
              ) : (
                <Pill size="sm" variant="ghost" onClick={() => setConfirmClear(true)}>
                  Clear history
                </Pill>
              )}
            </>
          ) : undefined
        }
      />
      <div
        className={cn(
          "grid grid-cols-[minmax(0,1fr)] gap-4",
          canManageConfig && "lg:grid-cols-[260px_minmax(0,1fr)]",
        )}
      >
        {canManageConfig ? (
          <nav
            aria-label="Threads"
            className="max-h-[40vh] overflow-y-auto rounded-3xl border border-grey-border p-2 lg:max-h-[70vh]"
          >
            {people.map((p) => (
              <button
                key={p.id}
                type="button"
                aria-pressed={p.id === threadId}
                onClick={() => setChosen(p.id)}
                className={cn(
                  "block w-full rounded-xl px-3 py-2 text-left text-[14px]",
                  p.id === threadId ? "bg-ink font-bold text-white" : "hover:bg-grey-fill",
                )}
              >
                {p.name}
                {p.title ? <span className="block text-[11px] opacity-70">{p.title}</span> : null}
              </button>
            ))}
          </nav>
        ) : null}
        <Thread
          threadId={threadId}
          personName={person?.name ?? null}
          messages={messages.data?.items ?? []}
          isLoading={messages.isLoading}
          error={messages.error}
          onSent={refresh}
        />
      </div>
    </>
  );
}

function Thread({
  threadId,
  personName,
  messages,
  isLoading,
  error,
  onSent,
}: {
  threadId: string;
  personName: string | null;
  messages: ChatSimulatorMessageResponse[];
  isLoading: boolean;
  error: unknown;
  onSent: () => Promise<unknown>;
}) {
  const [draft, setDraft] = useState("");
  const [replyTo, setReplyTo] = useState<ChatSimulatorMessageResponse | null>(null);
  const end = useRef<HTMLDivElement>(null);
  const sorted = [...messages].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const ids = new Set(sorted.map((m) => m.message_id));
  const top = sorted.filter((m) => !m.thread_id || !ids.has(m.thread_id));
  const replies = (id: string) => sorted.filter((m) => m.thread_id === id);

  useEffect(() => {
    end.current?.scrollIntoView({ block: "end" });
  }, [messages.length]);

  const send = useMutation({
    mutationFn: () =>
      apiClient.sendChatSimulatorUserMessage(threadId, {
        text: draft.trim(),
        developer_id: threadId,
        developer_name: personName,
        thread_id: replyTo?.message_id ?? null,
      }),
    onSuccess: async (result) => {
      setDraft("");
      const wasReply = replyTo !== null;
      setReplyTo(null);
      await onSent();
      toast.success(
        wasReply
          ? result.status === "resolved"
            ? "Reply sent; the request is resolved."
            : "Reply sent; the request is acknowledged."
          : result.started_checkin
            ? "Check-in opened and your update was filed against it."
            : "Update filed against the open check-in.",
      );
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (!threadId) {
    return (
      <p className="rounded-2xl bg-grey-fill px-4 py-3 text-[14px] text-grey-body">
        Pick someone to open their thread.
      </p>
    );
  }

  return (
    <section className="flex min-w-0 flex-col rounded-3xl border border-grey-border">
      <div className="max-h-[60vh] min-h-[320px] overflow-y-auto p-4">
        <PanelState
          needs="anyone with a member record"
          isLoading={isLoading}
          error={error}
          isEmpty={top.length === 0}
          emptyText="No messages yet. The bot asks at the tenant's check-in time."
        >
          <ul className="grid gap-3">
            {top.map((m) => (
              <li key={m.message_id}>
                <Bubble message={m} who={personName ?? "You"} />
                {m.direction === "bot" && m.purpose === REQUEST_PURPOSE ? (
                  <button
                    type="button"
                    className="ml-2 mt-1 text-[12px] font-bold text-magenta"
                    onClick={() => setReplyTo(m)}
                  >
                    Reply in thread
                  </button>
                ) : null}
                {replies(m.message_id).length > 0 ? (
                  <ul className="ml-6 mt-2 grid gap-2 border-l-2 border-grey-border pl-3">
                    {replies(m.message_id).map((r) => (
                      <li key={r.message_id}>
                        <Bubble message={r} who={personName ?? "You"} />
                      </li>
                    ))}
                  </ul>
                ) : null}
              </li>
            ))}
          </ul>
          <div ref={end} />
        </PanelState>
      </div>
      <form
        className="border-t border-grey-border p-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (draft.trim()) send.mutate();
        }}
      >
        {replyTo ? (
          <p className="mb-2 flex items-center gap-2 text-[12px] text-grey-body">
            Replying in thread to: “{replyTo.text.slice(0, 80)}”
            <button
              type="button"
              className="font-bold text-magenta"
              onClick={() => setReplyTo(null)}
            >
              Cancel
            </button>
          </p>
        ) : null}
        <div className="flex gap-2">
          <label htmlFor="chat-draft" className="sr-only">
            Message
          </label>
          <textarea
            id="chat-draft"
            className="min-h-12 flex-1 rounded-2xl border border-grey-border p-3 text-[14px]"
            placeholder="Wired the challenge flow. Still blocked on sandbox credentials. ETA slips 2 days."
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                if (draft.trim()) send.mutate();
              }
            }}
          />
          <Pill type="submit" size="md" disabled={!draft.trim() || send.isPending}>
            {send.isPending ? "Sending…" : "Send"}
          </Pill>
        </div>
      </form>
    </section>
  );
}

function Bubble({ message, who }: { message: ChatSimulatorMessageResponse; who: string }) {
  const bot = message.direction !== "user";
  return (
    <div className={cn("flex", bot ? "justify-start" : "justify-end")}>
      <div
        className={cn(
          "max-w-[80%] rounded-2xl px-4 py-2.5 text-[14px]",
          bot ? "bg-grey-fill text-ink" : "bg-magenta-tint text-ink",
        )}
      >
        <p className="whitespace-pre-line">{message.text}</p>
        <p className="mt-1 text-[11px] text-grey-secondary">
          {bot ? "OpenProgram" : who}
          {message.purpose
            ? ` · ${PURPOSE_WORDS[message.purpose] ?? message.purpose.replace(/_/g, " ")}`
            : ""}{" "}
          · {formatDay(message.created_at)} {formatTime(message.created_at)}
        </p>
      </div>
    </div>
  );
}
