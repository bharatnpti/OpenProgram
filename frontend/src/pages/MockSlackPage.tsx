import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare, Play, RefreshCw, Send, Trash2 } from "lucide-react";

import { apiClient } from "../api/client";
import type { ChatSimulatorMessageResponse } from "../api/schema";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Field } from "../components/ui/field";
import { Input } from "../components/ui/input";
import { Select } from "../components/ui/select";
import { Textarea } from "../components/ui/textarea";

const simulatorEnabled =
  import.meta.env.DEV || import.meta.env.VITE_ENABLE_CHAT_SIMULATOR === "true";

export function MockSlackPage() {
  const queryClient = useQueryClient();
  const [tenantId, setTenantId] = useState("demo");
  const [memberId, setMemberId] = useState("");
  const [chatExternalId, setChatExternalId] = useState("");
  const [selectedMessageId, setSelectedMessageId] = useState("");
  const [replyText, setReplyText] = useState("");

  const members = useQuery({
    queryKey: ["config", "members"],
    queryFn: apiClient.configMembers,
    enabled: simulatorEnabled,
  });
  const status = useQuery({
    queryKey: ["chat-simulator", "status"],
    queryFn: apiClient.chatSimulatorStatus,
    enabled: simulatorEnabled,
    retry: false,
  });
  const messages = useQuery({
    queryKey: ["chat-simulator", "messages"],
    queryFn: apiClient.chatSimulatorMessages,
    enabled: simulatorEnabled && status.isSuccess,
    refetchInterval: 5000,
  });

  const configuredMembers = useMemo(() => members.data ?? [], [members.data]);
  const selectedMember = configuredMembers.find((member) => member.id === memberId);
  const items = useMemo(() => messages.data?.items ?? [], [messages.data?.items]);
  const botMessages = items.filter((message) => message.direction === "bot");
  const selectedMessage = botMessages.find((message) => message.message_id === selectedMessageId);

  useEffect(() => {
    if (!memberId && configuredMembers.length > 0) {
      setMemberId(configuredMembers[0].id);
      setChatExternalId(configuredMembers[0].id);
    }
  }, [configuredMembers, memberId]);

  useEffect(() => {
    if (!selectedMessageId && botMessages.length > 0) {
      setSelectedMessageId(botMessages[botMessages.length - 1].message_id);
    }
  }, [botMessages, selectedMessageId]);

  const groups = useMemo(() => groupByUser(items), [items]);

  const invalidateSimulator = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ["chat-simulator"] }),
      queryClient.invalidateQueries({ queryKey: ["persona"] }),
    ]);
  };

  const dispatchMutation = useMutation({
    mutationFn: () =>
      apiClient.dispatchCheckin({
        tenant_id: tenantId,
        developer_id: memberId,
        developer_name: selectedMember?.name ?? memberId,
        chat_external_id: chatExternalId || memberId,
      }),
    onSuccess: invalidateSimulator,
  });

  const replyMutation = useMutation({
    mutationFn: () =>
      apiClient.replyChatSimulatorMessage(selectedMessageId, {
        text: replyText,
        received_at: new Date().toISOString(),
      }),
    onSuccess: async () => {
      setReplyText("");
      await invalidateSimulator();
    },
  });

  const resetMutation = useMutation({
    mutationFn: apiClient.resetChatSimulatorState,
    onSuccess: async () => {
      setSelectedMessageId("");
      await invalidateSimulator();
    },
  });

  if (!simulatorEnabled) {
    return (
      <main className="px-5 py-5">
        <header className="mb-4 border-b border-border pb-4">
          <h1 className="text-xl font-semibold">Mock Slack</h1>
        </header>
        <p className="text-sm text-muted-foreground">Unavailable in this frontend build.</p>
      </main>
    );
  }

  return (
    <main className="px-5 py-5">
      <header className="mb-4 flex flex-wrap items-center justify-between gap-3 border-b border-border pb-4">
        <div>
          <h1 className="text-xl font-semibold">Mock Slack</h1>
          <div className="mt-1 flex flex-wrap gap-2 text-xs text-muted-foreground">
            <span>{status.data?.tenant_id ?? tenantId}</span>
            <span>{status.data?.message_count ?? items.length} messages</span>
            {status.isError && <span className="text-red-600">disabled</span>}
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button type="button" onClick={() => void invalidateSimulator()}>
            <RefreshCw className="h-4 w-4" />
            Refresh
          </Button>
          <Button
            type="button"
            className="border-red-200 text-red-700 hover:bg-red-50"
            disabled={resetMutation.isPending}
            onClick={() => resetMutation.mutate()}
          >
            <Trash2 className="h-4 w-4" />
            Reset
          </Button>
        </div>
      </header>

      <div className="grid gap-4 xl:grid-cols-[340px_minmax(0,1fr)]">
        <section className="space-y-4">
          <div className="rounded border border-border bg-white p-4">
            <div className="mb-3 flex items-center gap-2">
              <Play className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold">Dispatch Check-In</h2>
            </div>
            <div className="space-y-3">
              <Field label="Tenant" htmlFor="sim-tenant">
                <Input
                  id="sim-tenant"
                  value={tenantId}
                  onChange={(event) => setTenantId(event.target.value)}
                />
              </Field>
              <Field label="Member" htmlFor="sim-member">
                <Select
                  id="sim-member"
                  value={memberId}
                  onChange={(event) => {
                    setMemberId(event.target.value);
                    setChatExternalId(event.target.value);
                  }}
                >
                  <option value="" disabled>
                    Select member
                  </option>
                  {configuredMembers.map((member) => (
                    <option key={member.id} value={member.id}>
                      {member.name}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Chat user" htmlFor="sim-chat-user">
                <Input
                  id="sim-chat-user"
                  value={chatExternalId}
                  onChange={(event) => setChatExternalId(event.target.value)}
                />
              </Field>
              <Button
                type="button"
                disabled={!memberId || dispatchMutation.isPending}
                onClick={() => dispatchMutation.mutate()}
              >
                <MessageSquare className="h-4 w-4" />
                Send DM
              </Button>
              {dispatchMutation.isError && <p className="text-xs text-red-600">Dispatch failed.</p>}
            </div>
          </div>

          <div className="rounded border border-border bg-white p-4">
            <div className="mb-3 flex items-center gap-2">
              <Send className="h-4 w-4 text-primary" />
              <h2 className="text-sm font-semibold">Reply</h2>
            </div>
            <div className="space-y-3">
              <Field label="Bot message" htmlFor="sim-reply-message">
                <Select
                  id="sim-reply-message"
                  value={selectedMessageId}
                  onChange={(event) => setSelectedMessageId(event.target.value)}
                >
                  <option value="" disabled>
                    Select message
                  </option>
                  {botMessages.map((message) => (
                    <option key={message.message_id} value={message.message_id}>
                      {message.user_id} · {message.purpose ?? message.message_id}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Message" htmlFor="sim-reply-text">
                <Textarea
                  id="sim-reply-text"
                  value={replyText}
                  onChange={(event) => setReplyText(event.target.value)}
                />
              </Field>
              <Button
                type="button"
                disabled={!selectedMessage || !replyText.trim() || replyMutation.isPending}
                onClick={() => replyMutation.mutate()}
              >
                <Send className="h-4 w-4" />
                Submit Reply
              </Button>
              {replyMutation.isError && <p className="text-xs text-red-600">Reply failed.</p>}
            </div>
          </div>
        </section>

        <section className="min-w-0 rounded border border-border bg-white">
          <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-3">
            <h2 className="text-sm font-semibold">Messages</h2>
            {messages.isFetching && <span className="text-xs text-muted-foreground">Loading</span>}
          </div>
          {groups.length === 0 ? (
            <p className="px-4 py-6 text-sm text-muted-foreground">No messages.</p>
          ) : (
            <div className="divide-y divide-border">
              {groups.map(([userId, userMessages]) => (
                <div key={userId} className="px-4 py-3">
                  <div className="mb-3 flex items-center justify-between gap-3">
                    <div>
                      <div className="text-sm font-medium">{userId}</div>
                      <div className="text-xs text-muted-foreground">
                        {userMessages[0]?.channel_id}
                      </div>
                    </div>
                    <Badge tone="neutral">{userMessages.length}</Badge>
                  </div>
                  <div className="space-y-2">
                    {userMessages.map((message) => (
                      <button
                        key={message.message_id}
                        type="button"
                        className="w-full rounded border border-border px-3 py-2 text-left transition hover:border-primary/40"
                        onClick={() => {
                          if (message.direction === "bot") {
                            setSelectedMessageId(message.message_id);
                          }
                        }}
                      >
                        <div className="mb-1 flex flex-wrap items-center gap-2">
                          <Badge tone={message.direction === "bot" ? "info" : "success"}>
                            {message.direction}
                          </Badge>
                          {message.purpose && <Badge tone="neutral">{message.purpose}</Badge>}
                          <span className="text-xs text-muted-foreground">
                            {formatTime(message.created_at)}
                          </span>
                        </div>
                        <p className="whitespace-pre-wrap text-sm">{message.text}</p>
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </section>
      </div>
    </main>
  );
}

function groupByUser(
  messages: ChatSimulatorMessageResponse[],
): [string, ChatSimulatorMessageResponse[]][] {
  const groups = new Map<string, ChatSimulatorMessageResponse[]>();
  messages.forEach((message) => {
    const existing = groups.get(message.user_id) ?? [];
    existing.push(message);
    groups.set(message.user_id, existing);
  });
  return Array.from(groups.entries());
}

function formatTime(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).format(new Date(value));
}
