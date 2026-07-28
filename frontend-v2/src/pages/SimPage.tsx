import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../api/client";
import type { ChatSimulatorMessageResponse } from "../api/schema";
import { Card } from "../components/ui/Card";
import { Modal } from "../components/ui/Modal";
import { Pill } from "../components/ui/Pill";
import { RagChip } from "../components/ui/RagChip";
import { cn } from "../lib/utils";

const simulatorFrontendEnabled =
  import.meta.env.DEV || import.meta.env.VITE_ENABLE_CHAT_SIMULATOR === "true";

export function SimPage() {
  const queryClient = useQueryClient();
  const [memberId, setMemberId] = useState("");
  const [composerText, setComposerText] = useState("");
  const [resetOpen, setResetOpen] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  const members = useQuery({
    queryKey: ["config", "members"],
    queryFn: apiClient.configMembers,
    enabled: simulatorFrontendEnabled,
  });
  const status = useQuery({
    queryKey: ["chat-simulator", "status"],
    queryFn: apiClient.chatSimulatorStatus,
    enabled: simulatorFrontendEnabled,
    retry: false,
  });
  const messages = useQuery({
    queryKey: ["chat-simulator", "messages"],
    queryFn: apiClient.chatSimulatorMessages,
    enabled: simulatorFrontendEnabled && status.isSuccess,
    refetchInterval: 5000,
  });

  const configuredMembers = useMemo(() => members.data ?? [], [members.data]);
  const selectedMember = configuredMembers.find((member) => member.id === memberId);
  const items = useMemo(() => sortByCreatedAt(messages.data?.items ?? []), [messages.data?.items]);
  const botMessages = useMemo(
    () => items.filter((message) => message.direction === "bot"),
    [items],
  );
  const activeBotMessage = botMessages[botMessages.length - 1];

  useEffect(() => {
    if (configuredMembers.length === 0) {
      if (memberId) setMemberId("");
      return;
    }
    const selectedMemberExists = configuredMembers.some((member) => member.id === memberId);
    if (!memberId || !selectedMemberExists) {
      setMemberId(configuredMembers[0].id);
    }
  }, [configuredMembers, memberId]);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: "end" });
  }, [items.length]);

  const invalidateSimulator = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["config", "members"] }),
      queryClient.invalidateQueries({ queryKey: ["chat-simulator"] }),
      queryClient.invalidateQueries({ queryKey: ["persona"] }),
    ]);
  };

  const dispatchMutation = useMutation({
    mutationFn: () =>
      apiClient.dispatchCheckin({
        tenant_id: "demo",
        developer_id: memberId,
        developer_name: selectedMember?.name ?? memberId,
        chat_external_id: memberId,
      }),
    onSuccess: async () => {
      await invalidateSimulator();
      toast.success("Check-in dispatched.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const replyMutation = useMutation({
    mutationFn: () => {
      if (!activeBotMessage) throw new Error("No bot message to reply to.");
      return apiClient.replyChatSimulatorMessage(activeBotMessage.message_id, {
        text: composerText.trim(),
        received_at: new Date().toISOString(),
      });
    },
    onSuccess: async () => {
      setComposerText("");
      await invalidateSimulator();
      toast.success("Reply submitted.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const resetMutation = useMutation({
    mutationFn: apiClient.resetChatSimulatorState,
    onSuccess: async () => {
      setComposerText("");
      setResetOpen(false);
      await invalidateSimulator();
      toast.success("Simulator state reset.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (!simulatorFrontendEnabled) {
    return <DisabledState reason="Unavailable in this frontend build." />;
  }

  if (status.isSuccess && !status.data.enabled) {
    return <DisabledState reason="The simulator is disabled on the backend for this tenant." />;
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="animate-op-fade-up flex flex-wrap items-start justify-between gap-4">
        <div>
          <RagChip tone="warning">Local test surface — no live Slack</RagChip>
          <h1 className="mt-3 text-[40px] font-extrabold leading-[1.05]">Chat simulator</h1>
          <p className="mt-2.5 max-w-[640px] text-[18px] text-grey-secondary">
            Play the developer side of the check-in conversation and watch it land in the rollup.
          </p>
        </div>
        <div className="flex gap-2 pt-1">
          <Pill variant="ghost" size="sm" onClick={() => invalidateSimulator()}>
            <RefreshCw size={14} />
            Refresh
          </Pill>
          <Pill
            variant="ghost"
            size="sm"
            className="border-rag-red text-rag-red hover:bg-rag-red-bg"
            disabled={resetMutation.isPending}
            onClick={() => setResetOpen(true)}
          >
            <Trash2 size={14} />
            Reset
          </Pill>
        </div>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-[320px_minmax(0,1fr)]">
        <Card variant="grey" padding="p-6" animateDelay={70} className="lg:sticky lg:top-24">
          <h3 className="text-[16px] font-bold">Dispatch check-in</h3>
          <label className="mt-4 block text-[13px] font-bold text-grey-secondary" htmlFor="sim-member">
            Member
            <select
              id="sim-member"
              value={memberId}
              onChange={(event) => setMemberId(event.target.value)}
              disabled={configuredMembers.length === 0}
              className="mt-1.5 h-12 w-full rounded-2xl border border-grey-border bg-white px-3.5 text-[15px] font-medium text-ink outline-none focus:border-ink disabled:text-grey-disabled"
            >
              {configuredMembers.length === 0 ? <option value="">No members configured</option> : null}
              {configuredMembers.map((member) => (
                <option key={member.id} value={member.id}>
                  {member.name}
                </option>
              ))}
            </select>
          </label>
          <Pill
            variant="primary"
            size="lg"
            className="mt-4 w-full"
            disabled={!memberId || dispatchMutation.isPending}
            onClick={() => dispatchMutation.mutate()}
          >
            {dispatchMutation.isPending ? "Sending…" : "Send check-in DM"}
          </Pill>
          <p className="mt-4 text-[13px] leading-relaxed text-grey-secondary">
            The bot asks three questions: what moved, what is planned, and what blocks you.
            Replies parse into structured status.
          </p>
        </Card>

        <Card padding="p-0" animateDelay={140} className="flex min-h-[480px] flex-col">
          <div className="flex items-center gap-3 border-b border-grey-fill px-6 py-4">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-full bg-magenta text-[13px] font-extrabold text-white">
              OP
            </div>
            <div className="min-w-0">
              <div className="truncate text-[15px] font-bold">
                OpenProgram bot &harr; {selectedMember?.name ?? "developer"}
              </div>
              <div className="text-[12px] text-grey-secondary">
                direct message &middot; {status.data?.tenant_id ?? "demo"} tenant
                {messages.data ? ` · ${items.length} messages` : ""}
              </div>
            </div>
          </div>

          <div className="max-h-[calc(100vh-26rem)] flex-1 overflow-y-auto px-6 py-5">
            {messages.isLoading ? (
              <p className="text-[14px] text-grey-secondary">Loading conversation&hellip;</p>
            ) : items.length === 0 ? (
              <div className="flex h-full flex-col items-center justify-center gap-1 text-center">
                <p className="text-[15px] font-bold">No messages yet</p>
                <p className="text-[13px] text-grey-secondary">
                  Dispatch a check-in to start the conversation.
                </p>
              </div>
            ) : (
              <div className="flex min-h-full flex-col justify-end gap-3">
                {items.map((message) => (
                  <MessageBubble key={message.message_id} message={message} />
                ))}
                <div ref={bottomRef} />
              </div>
            )}
          </div>

          <form
            className="flex gap-3 border-t border-grey-fill px-6 py-4"
            onSubmit={(event) => {
              event.preventDefault();
              if (activeBotMessage && composerText.trim()) replyMutation.mutate();
            }}
          >
            <input
              value={composerText}
              onChange={(event) => setComposerText(event.target.value)}
              disabled={!activeBotMessage || replyMutation.isPending}
              placeholder={
                activeBotMessage
                  ? `Reply as ${selectedMember?.name ?? "developer"}…`
                  : "Dispatch a check-in to start the conversation…"
              }
              className="h-12 flex-1 rounded-full border border-grey-border bg-white px-5 text-[15px] outline-none focus:border-magenta disabled:bg-grey-fill disabled:text-grey-secondary"
            />
            <Pill
              type="submit"
              variant="dark"
              size="lg"
              disabled={!activeBotMessage || !composerText.trim() || replyMutation.isPending}
            >
              {replyMutation.isPending ? "Sending…" : "Send"}
            </Pill>
          </form>
        </Card>
      </div>

      <Modal open={resetOpen} onOpenChange={setResetOpen} title="Reset simulator state?">
        <p className="text-[14px] leading-relaxed text-grey-secondary">
          This clears local simulator messages and reply state. It does not touch production
          Slack.
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
            {resetMutation.isPending ? "Resetting…" : "Reset"}
          </Pill>
        </div>
      </Modal>
    </div>
  );
}

function MessageBubble({ message }: { message: ChatSimulatorMessageResponse }) {
  const isUser = message.direction === "user";
  return (
    <div className={cn("animate-op-pop flex", isUser ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[74%] px-4 py-2.5 text-[15px] leading-snug",
          isUser
            ? "rounded-[20px_20px_4px_20px] bg-magenta text-white"
            : "rounded-[20px_20px_20px_4px] bg-grey-fill text-ink",
        )}
      >
        <p className="whitespace-pre-wrap">{message.text}</p>
        <div
          className={cn(
            "mt-1 text-right text-[11px]",
            isUser ? "text-white/70" : "text-grey-secondary",
          )}
        >
          {formatTime(message.created_at)}
        </div>
      </div>
    </div>
  );
}

function DisabledState({ reason }: { reason: string }) {
  return (
    <div className="flex flex-col gap-6">
      <div className="animate-op-fade-up">
        <RagChip tone="warning">Local test surface — no live Slack</RagChip>
        <h1 className="mt-3 text-[40px] font-extrabold leading-[1.05]">Chat simulator</h1>
      </div>
      <Card padding="p-8" className="text-center">
        <h2 className="text-[18px] font-bold">Simulator disabled</h2>
        <p className="mt-2 text-[14px] text-grey-secondary">{reason}</p>
      </Card>
    </div>
  );
}

function sortByCreatedAt(
  messages: ChatSimulatorMessageResponse[],
): ChatSimulatorMessageResponse[] {
  return [...messages].sort(
    (a, b) => new Date(a.created_at).getTime() - new Date(b.created_at).getTime(),
  );
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(
    new Date(value),
  );
}

function errorMessage(error: unknown): string {
  if (error instanceof Error) return error.message;
  return "Operation failed.";
}
