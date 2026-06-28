import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare, RefreshCw, Send, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";

import { apiClient } from "../api/client";
import type { ChatSimulatorMessageResponse } from "../api/schema";
import {
  DataPanel,
  EmptyState,
  KpiCard,
  PageHeader,
  QueryState,
} from "../components/ops/primitives";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { ConfirmDialog } from "../components/ui/confirm-dialog";
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
  const [resetOpen, setResetOpen] = useState(false);

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
  const groups = useMemo(() => groupByUser(items), [items]);

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
    onSuccess: async () => {
      await invalidateSimulator();
      toast.success("Check-in dispatched.");
    },
    onError: (error) => toast.error(errorMessage(error)),
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
      toast.success("Reply submitted.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const resetMutation = useMutation({
    mutationFn: apiClient.resetChatSimulatorState,
    onSuccess: async () => {
      setSelectedMessageId("");
      await invalidateSimulator();
      toast.success("Simulator state reset.");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  if (!simulatorEnabled) {
    return (
      <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
        <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
          <PageHeader
            eyebrow="Test support"
            title="Mock Slack"
            description="Unavailable in this frontend build."
          />
          <EmptyState
            title="Simulator disabled"
            description="Enable the local chat simulator explicitly for manual E2E testing."
          />
        </div>
      </main>
    );
  }

  return (
    <main className="min-h-screen px-4 py-4 sm:px-5 lg:px-6">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-5">
        <PageHeader
          eyebrow={
            <span className="inline-flex items-center gap-2 text-warning">
              <ShieldAlert className="h-4 w-4" />
              Local admin test surface
            </span>
          }
          title="Mock Slack"
          description="Dispatch check-ins, inspect simulator messages, and inject Slack-shaped replies without live Slack credentials."
          actions={
            <div className="flex flex-wrap gap-2">
              <Button type="button" variant="outline" onClick={() => void invalidateSimulator()}>
                <RefreshCw className="h-4 w-4" />
                Refresh
              </Button>
              <Button
                type="button"
                variant="danger"
                disabled={resetMutation.isPending}
                onClick={() => setResetOpen(true)}
              >
                <Trash2 className="h-4 w-4" />
                Reset
              </Button>
            </div>
          }
        />

        <section className="grid gap-3 md:grid-cols-3">
          <KpiCard
            label="Tenant"
            value={status.data?.tenant_id ?? tenantId}
            detail="simulator"
            tone="info"
          />
          <KpiCard
            label="Messages"
            value={messages.data ? items.length : (status.data?.message_count ?? items.length)}
            detail={messages.isFetching ? "polling" : "current"}
          />
          <KpiCard label="Members" value={configuredMembers.length} detail="configured" />
        </section>

        <div className="grid gap-4 xl:grid-cols-[360px_minmax(0,1fr)]">
          <section className="space-y-4">
            <DataPanel
              title="Dispatch Check-In"
              description="Trigger the workflow through the admin dispatch API."
            >
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
                  variant="primary"
                  disabled={!memberId || dispatchMutation.isPending}
                  onClick={() => dispatchMutation.mutate()}
                >
                  <MessageSquare className="h-4 w-4" />
                  Send DM
                </Button>
              </div>
            </DataPanel>

            <DataPanel
              title="Reply"
              description="Submit a user reply against the selected bot message."
            >
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
                        {message.user_id} - {message.purpose ?? message.message_id}
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
                  variant="primary"
                  disabled={!selectedMessage || !replyText.trim() || replyMutation.isPending}
                  onClick={() => replyMutation.mutate()}
                >
                  <Send className="h-4 w-4" />
                  Submit reply
                </Button>
              </div>
            </DataPanel>
          </section>

          <DataPanel
            title="Simulator Message Timeline"
            description="Raw simulator messages are visible only on this local test page."
            bodyClassName="p-0"
          >
            <QueryState query={messages}>
              {() =>
                groups.length === 0 ? (
                  <div className="p-4">
                    <EmptyState
                      title="No messages"
                      description="Dispatch a check-in to populate the simulator timeline."
                    />
                  </div>
                ) : (
                  <div className="max-h-[calc(100vh-14rem)] divide-y divide-border overflow-y-auto scrollbar-thin">
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
                              className="w-full rounded-md border border-border px-3 py-2 text-left transition-colors hover:border-primary/50 hover:bg-surface-muted/40"
                              onClick={() => {
                                if (message.direction === "bot")
                                  setSelectedMessageId(message.message_id);
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
                )
              }
            </QueryState>
          </DataPanel>
        </div>

        <ConfirmDialog
          open={resetOpen}
          onOpenChange={setResetOpen}
          title="Reset simulator state?"
          description="This clears local simulator messages and selected reply state. It does not touch production Slack."
          confirmLabel="Reset"
          destructive
          onConfirm={() => resetMutation.mutate()}
        />
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

function errorMessage(error: unknown) {
  if (error instanceof Error) return error.message;
  return "Operation failed.";
}
