import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, RefreshCw, Send, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../api/client";
import type { ChatSimulatorMessageResponse, DevUserResponse } from "../api/schema";
import { initialsFor, useRole } from "../app/role";
import { Card } from "../components/ui/Card";
import { Modal } from "../components/ui/Modal";
import { Pill } from "../components/ui/Pill";
import { RagChip } from "../components/ui/RagChip";
import { cn } from "../lib/utils";

/**
 * The check-in conversation, standing in for the chat workspace.
 *
 * Everyone can read and answer their own thread. A config manager also gets the
 * roster on the left and can open anyone's conversation, which is what makes a
 * whole team's check-in day demonstrable from one browser.
 */
export function ChatPage() {
  const queryClient = useQueryClient();
  const { actingAs, people, canAccessAdmin, chatEnabled } = useRole();
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [resetOpen, setResetOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  // Without config rights the only readable thread is your own.
  const threadId = canAccessAdmin ? (selectedId ?? actingAs?.id ?? null) : (actingAs?.id ?? null);
  const threadPerson = useMemo(
    () => people.find((person) => person.id === threadId) ?? null,
    [people, threadId],
  );
  const isOwnThread = threadId === actingAs?.id;

  const status = useQuery({
    queryKey: ["chat", "status"],
    queryFn: apiClient.chatSimulatorStatus,
    enabled: chatEnabled,
    retry: false,
  });

  const messages = useQuery({
    queryKey: ["chat", "messages", threadId],
    queryFn: () => apiClient.chatSimulatorMessages(threadId ?? undefined),
    enabled: chatEnabled && status.isSuccess && threadId !== null,
    refetchInterval: 5000,
  });

  const items = useMemo(() => sortByCreatedAt(messages.data?.items ?? []), [messages.data?.items]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [items.length, threadId]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["chat"] }),
      // A reply changes status, focus, and every rollup that reads it.
      queryClient.invalidateQueries({ queryKey: ["persona"] }),
      queryClient.invalidateQueries({ queryKey: ["today"] }),
    ]);
  };

  const sendMutation = useMutation({
    mutationFn: () => {
      if (!threadId) {
        throw new Error("No conversation is selected.");
      }
      return apiClient.sendChatSimulatorUserMessage(threadId, {
        text: draft.trim(),
        developer_id: threadId,
        developer_name: threadPerson?.name ?? null,
        // No received_at: the server stamps it. Sending the browser's clock put
        // a live reply *before* the bot question it answers -- the transcript is
        // ordered by that value, and opening a check-in for an unprompted update
        // takes a few seconds -- so the thread read as the bot asking after
        // being told. The field stays optional for seeding and tests, which do
        // need to place a reply at a chosen historical time.
      });
    },
    onSuccess: async (result) => {
      setDraft("");
      await refresh();
      toast.success(
        result.started_checkin
          ? "Check-in opened and your update was filed against it."
          : "Update filed against the open check-in.",
      );
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const askMutation = useMutation({
    mutationFn: () => {
      if (!threadId) {
        throw new Error("No conversation is selected.");
      }
      return apiClient.dispatchCheckin({
        tenant_id: status.data?.tenant_id ?? "demo",
        developer_id: threadId,
        developer_name: threadPerson?.name ?? threadId,
        chat_external_id: threadId,
      });
    },
    onSuccess: async () => {
      await refresh();
      toast.success("Check-in requested — the bot will ask in a moment.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const resetMutation = useMutation({
    mutationFn: apiClient.resetChatSimulatorState,
    onSuccess: async () => {
      setDraft("");
      setResetOpen(false);
      await refresh();
      toast.success("Conversation history cleared.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (!chatEnabled) {
    return <DisabledState reason="This tenant does not run the built-in chat." />;
  }
  if (status.isSuccess && !status.data.enabled) {
    return <DisabledState reason="Chat is disabled on the backend for this tenant." />;
  }
  if (status.isError) {
    return (
      <DisabledState reason="This tenant is not running the built-in chat, so there is no conversation to show." />
    );
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="animate-op-fade-up flex flex-wrap items-start justify-between gap-4">
        <div>
          <RagChip tone="warning">Built-in chat — no external workspace connected</RagChip>
          <h1 className="mt-3 text-[40px] font-extrabold leading-[1.05]">Check-in chat</h1>
          <p className="mt-2.5 max-w-[660px] text-[18px] text-grey-secondary">
            The bot asks what moved, what is planned, and what is blocking. Replies parse into
            structured status and roll straight up the graph.
          </p>
        </div>
        <div className="flex gap-2 pt-1">
          <Pill variant="ghost" size="sm" onClick={() => refresh()}>
            <RefreshCw size={14} />
            Refresh
          </Pill>
          {canAccessAdmin ? (
            <Pill
              variant="ghost"
              size="sm"
              className="border-rag-red text-rag-red hover:bg-rag-red-bg"
              disabled={resetMutation.isPending}
              onClick={() => setResetOpen(true)}
            >
              <Trash2 size={14} />
              Clear history
            </Pill>
          ) : null}
        </div>
      </div>

      <div
        className={cn(
          "grid items-start gap-6",
          canAccessAdmin ? "lg:grid-cols-[300px_minmax(0,1fr)]" : "",
        )}
      >
        {canAccessAdmin ? (
          <Roster
            people={people}
            selectedId={threadId}
            ownId={actingAs?.id ?? null}
            onSelect={setSelectedId}
          />
        ) : null}

        <Card padding="p-0" animateDelay={140} className="flex min-h-[520px] flex-col">
          <div className="flex items-center gap-3 border-b border-grey-fill px-6 py-4">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-magenta text-[13px] font-extrabold text-white">
              OP
            </div>
            <div className="min-w-0 flex-1">
              <div className="truncate text-[15px] font-bold">
                OpenProgram &harr; {threadPerson?.name ?? threadId ?? "nobody"}
              </div>
              <div className="truncate text-[12px] text-grey-secondary">
                direct message
                {threadPerson?.title ? ` · ${threadPerson.title}` : ""}
                {items.length > 0 ? ` · ${items.length} messages` : ""}
              </div>
            </div>
            {/* Dispatching a check-in targets an arbitrary developer, so it is
                an admin capability. Everyone else needs no button: sending a
                message opens their check-in on its own. */}
            {canAccessAdmin ? (
              <Pill
                variant="ghost"
                size="sm"
                disabled={!threadId || askMutation.isPending}
                onClick={() => askMutation.mutate()}
              >
                <Bell size={14} />
                {askMutation.isPending ? "Asking…" : "Request check-in"}
              </Pill>
            ) : null}
          </div>

          <div className="max-h-[calc(100vh-28rem)] min-h-[300px] flex-1 overflow-y-auto px-6 py-5">
            {threadId === null ? (
              <EmptyThread
                title="No conversation selected"
                body="Pick someone from the roster to open their check-in thread."
              />
            ) : messages.isLoading ? (
              <p className="text-[14px] text-grey-secondary">Loading conversation&hellip;</p>
            ) : items.length === 0 ? (
              <EmptyThread
                title="No messages yet"
                body={
                  isOwnThread
                    ? "Type your update below — the check-in opens automatically."
                    : "Request a check-in, or type an update on this person's behalf."
                }
              />
            ) : (
              <div className="flex min-h-full flex-col justify-end gap-3">
                {groupByDay(items).map(([day, dayItems]) => (
                  <div key={day} className="flex flex-col gap-3">
                    <DayDivider day={day} />
                    {dayItems.map((message) => (
                      <MessageBubble
                        key={message.message_id}
                        message={message}
                        authorName={threadPerson?.name ?? threadId}
                      />
                    ))}
                  </div>
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <form
            className="flex items-center gap-3 border-t border-grey-fill px-6 py-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (threadId && draft.trim()) {
                sendMutation.mutate();
              }
            }}
          >
            <input
              value={draft}
              onChange={(event) => setDraft(event.target.value)}
              disabled={!threadId || sendMutation.isPending}
              placeholder={
                threadId
                  ? isOwnThread
                    ? "Share your status… e.g. “Shipped the capture path. Blocked on sandbox creds. ETA slips 2 days.”"
                    : `Write as ${threadPerson?.name ?? threadId}…`
                  : "Select a conversation…"
              }
              className="h-12 min-w-0 flex-1 rounded-full border border-grey-border bg-white px-5 text-[15px] outline-none focus:border-magenta disabled:bg-grey-fill disabled:text-grey-secondary"
            />
            <Pill
              type="submit"
              variant="dark"
              size="lg"
              disabled={!threadId || !draft.trim() || sendMutation.isPending}
            >
              <Send size={14} />
              {sendMutation.isPending ? "Sending…" : "Send"}
            </Pill>
          </form>
        </Card>
      </div>

      <Modal open={resetOpen} onOpenChange={setResetOpen} title="Clear conversation history?">
        <p className="text-[14px] leading-relaxed text-grey-secondary">
          This deletes the stored chat messages for every person in this tenant. Check-ins and
          statuses already derived from them are left untouched.
        </p>
        <div className="mt-5 flex justify-end gap-3">
          <Pill variant="ghost" size="md" onClick={() => setResetOpen(false)}>
            Cancel
          </Pill>
          <Pill
            variant="dark"
            size="md"
            className="bg-rag-red hover:bg-[#a10b00]"
            disabled={resetMutation.isPending}
            onClick={() => resetMutation.mutate()}
          >
            {resetMutation.isPending ? "Clearing…" : "Clear"}
          </Pill>
        </div>
      </Modal>
    </div>
  );
}

function Roster({
  people,
  selectedId,
  ownId,
  onSelect,
}: {
  people: DevUserResponse[];
  selectedId: string | null;
  ownId: string | null;
  onSelect: (id: string) => void;
}) {
  return (
    <Card variant="grey" padding="p-3" animateDelay={70} className="lg:sticky lg:top-24">
      <h3 className="px-2.5 pb-2 pt-1.5 text-[13px] font-bold uppercase tracking-wide text-grey-secondary">
        Conversations
      </h3>
      <div className="max-h-[520px] overflow-y-auto">
        {people.map((person) => (
          <button
            key={person.id}
            type="button"
            onClick={() => onSelect(person.id)}
            className={cn(
              "flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left",
              person.id === selectedId ? "bg-white shadow-sm" : "hover:bg-white/60",
            )}
          >
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-grey-border text-[11px] font-extrabold text-ink">
              {initialsFor(person.name)}
            </span>
            <span className="min-w-0 flex-1">
              <span
                className={cn(
                  "block truncate text-[14px] leading-tight",
                  person.id === selectedId ? "font-bold" : "font-medium",
                )}
              >
                {person.name}
              </span>
              <span className="block truncate text-[11px] leading-tight text-grey-secondary">
                {person.title ?? person.id}
              </span>
            </span>
            {person.id === ownId ? (
              <span className="shrink-0 rounded-md bg-magenta/10 px-1.5 py-0.5 text-[10px] font-bold uppercase text-magenta">
                You
              </span>
            ) : null}
          </button>
        ))}
      </div>
    </Card>
  );
}

function MessageBubble({
  message,
  authorName,
}: {
  message: ChatSimulatorMessageResponse;
  authorName: string;
}) {
  const isUser = message.direction === "user";
  return (
    <div className={cn("animate-op-pop flex", isUser ? "justify-end" : "justify-start")}>
      <div className="max-w-[74%]">
        <div
          className={cn(
            "px-4 py-2.5 text-[15px] leading-snug",
            isUser
              ? "rounded-[20px_20px_4px_20px] bg-magenta text-white"
              : "rounded-[20px_20px_20px_4px] bg-grey-fill text-ink",
          )}
        >
          <p className="whitespace-pre-wrap">{message.text}</p>
        </div>
        <div
          className={cn(
            "mt-1 px-1 text-[11px] text-grey-secondary",
            isUser ? "text-right" : "text-left",
          )}
        >
          {isUser ? authorName : "OpenProgram"} · {formatTime(message.created_at)}
        </div>
      </div>
    </div>
  );
}

function DayDivider({ day }: { day: string }) {
  return (
    <div className="flex items-center gap-3 py-1">
      <span className="h-px flex-1 bg-grey-fill" />
      <span className="text-[11px] font-bold uppercase tracking-wide text-grey-secondary">
        {formatDay(day)}
      </span>
      <span className="h-px flex-1 bg-grey-fill" />
    </div>
  );
}

function EmptyThread({ title, body }: { title: string; body: string }) {
  return (
    <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
      <p className="text-[15px] font-bold">{title}</p>
      <p className="max-w-[360px] text-[13px] text-grey-secondary">{body}</p>
    </div>
  );
}

function DisabledState({ reason }: { reason: string }) {
  return (
    <div className="flex flex-col gap-6">
      <div className="animate-op-fade-up">
        <RagChip tone="warning">Built-in chat</RagChip>
        <h1 className="mt-3 text-[40px] font-extrabold leading-[1.05]">Check-in chat</h1>
      </div>
      <Card padding="p-8" className="text-center">
        <h2 className="text-[18px] font-bold">Chat unavailable</h2>
        <p className="mt-2 text-[14px] text-grey-secondary">{reason}</p>
      </Card>
    </div>
  );
}

function sortByCreatedAt(messages: ChatSimulatorMessageResponse[]): ChatSimulatorMessageResponse[] {
  return [...messages].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );
}

function groupByDay(
  messages: ChatSimulatorMessageResponse[],
): [string, ChatSimulatorMessageResponse[]][] {
  const groups = new Map<string, ChatSimulatorMessageResponse[]>();
  for (const message of messages) {
    const day = message.created_at.slice(0, 10);
    const bucket = groups.get(day);
    if (bucket) {
      bucket.push(message);
    } else {
      groups.set(day, [message]);
    }
  }
  return [...groups.entries()];
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(
    new Date(value),
  );
}

function formatDay(day: string): string {
  const date = new Date(`${day}T00:00:00`);
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const diffDays = Math.round((today.getTime() - date.getTime()) / 86_400_000);
  if (diffDays === 0) {
    return "Today";
  }
  if (diffDays === 1) {
    return "Yesterday";
  }
  return new Intl.DateTimeFormat(undefined, {
    weekday: "short",
    day: "numeric",
    month: "short",
  }).format(date);
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) {
    return error.message;
  }
  return "Operation failed.";
}
