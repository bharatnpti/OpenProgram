import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, KeyRound } from "lucide-react";
import { useState, type ReactNode } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  ConnectionResponse,
  ConnectionTestResponse,
  ConnectorFieldDto,
} from "../../api/schema";
import { ConfirmDialog } from "../../components/Dialogs";
import { PanelState } from "../../components/PanelState";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import type { BadgeTone } from "../../lib/status";
import { cn } from "../../lib/utils";
import { AdminDialog, TabIntro, hintClass, inputClass, labelClass } from "./AdminBits";
import { errorText, stampLabel as when } from "./adminWords";
import {
  CONNECTION_GROUPS,
  type ConnectionForm,
  type ConnectionState,
  connectionState,
  formFromConnection,
  groupOf,
  missingRequired,
  optionLabel,
  runsOnServerSettings,
  savePayload,
  savedBy,
  testBlock,
  testPayload,
  visibleFields,
  visibleOptions,
  withValidOptions,
} from "./connectionForm";

const CONNECTIONS_KEY = ["config", "integrations"] as const;

const STATE_WORDS: Record<ConnectionState, { tone: BadgeTone; word: string }> = {
  on: { tone: "success", word: "on" },
  off: { tone: "neutral", word: "off" },
  environment: { tone: "info", word: "server settings" },
  not_set_up: { tone: "neutral", word: "not set up" },
};

/**
 * The systems OpenProgram connects to, set up per tenant: where day reports go
 * (chat, email, Teams) and where work is read from (Jira, a Git host).
 *
 * Secrets go in and never come back: the API names which secret fields hold a
 * value and nothing more, so a stored one reads "stored" and is kept unless it
 * is typed over or cleared.
 */
export function IntegrationsTab() {
  const connections = useQuery({
    queryKey: CONNECTIONS_KEY,
    queryFn: () => apiClient.connections(),
  });
  const [editing, setEditing] = useState<string | null>(null);
  const editingConnection = connections.data?.find((item) => item.connector === editing) ?? null;

  return (
    <PanelState
      needs="an admin"
      isLoading={connections.isLoading}
      error={connections.error}
      onRetry={() => void connections.refetch()}
      isEmpty={(connections.data ?? []).length === 0}
      emptyText="This server offers no connections to set up."
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-5">
        <TabIntro>
          Connect the systems OpenProgram reads from and sends through. Tokens and passwords are
          stored encrypted and are never shown again. A system not turned on here uses the server's
          own settings, if it has any.
        </TabIntro>
        {CONNECTION_GROUPS.map((group) => {
          const items = (connections.data ?? []).filter((item) => groupOf(item) === group.key);
          if (items.length === 0) return null;
          return (
            <section key={group.key} aria-labelledby={`group-${group.key}`} className="grid gap-3">
              <div>
                <h3 id={`group-${group.key}`} className="text-[17px] font-extrabold">
                  {group.title}
                </h3>
                {group.note ? (
                  <p className="text-[13px] text-grey-secondary">{group.note}</p>
                ) : null}
              </div>
              <div className="grid grid-cols-[minmax(0,1fr)] gap-3 lg:grid-cols-2">
                {items.map((connection) => (
                  <ConnectorCard
                    key={connection.connector}
                    connection={connection}
                    onEdit={() => setEditing(connection.connector)}
                  />
                ))}
              </div>
            </section>
          );
        })}
      </div>
      {editingConnection ? (
        <ConnectionDialog
          key={editingConnection.connector}
          connection={editingConnection}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </PanelState>
  );
}

function ConnectorCard({
  connection,
  onEdit,
}: {
  connection: ConnectionResponse;
  onEdit: () => void;
}) {
  const state = connectionState(connection);
  const test = connection.last_test;
  const flavour = connection.connector === "jira" ? optionLabel(connection, "deployment") : null;
  return (
    <section className="grid min-w-0 content-start gap-2 rounded-3xl border border-grey-border bg-white p-5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h4 className="text-[16px] font-extrabold">{connection.name}</h4>
          <p className="mt-0.5 text-[13px] text-grey-body">{connection.description}</p>
          {flavour && connection.configured ? (
            <p className="text-[12px] text-grey-secondary">{flavour}</p>
          ) : null}
        </div>
        <RagChip tone={STATE_WORDS[state].tone} dot className="h-6 flex-none px-2.5 text-[12px]">
          {STATE_WORDS[state].word}
        </RagChip>
      </div>
      {test ? (
        <p className={cn("text-[13px]", test.ok ? "text-rag-green" : "text-rag-red")}>
          {test.ok ? "Last test passed" : "Last test failed"}: {test.message}{" "}
          <span className="text-grey-secondary">· {when(test.tested_at)}</span>
        </p>
      ) : state === "on" ? (
        <p className="text-[13px] text-grey-secondary">Not tested since it was last saved.</p>
      ) : null}
      {connection.secrets_set.length > 0 ? (
        <p className="flex items-center gap-1 text-[12px] text-grey-secondary">
          <KeyRound size={12} aria-hidden />
          {connection.secrets_set.length === 1
            ? "1 secret stored"
            : `${connection.secrets_set.length} secrets stored`}
          , never shown
        </p>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
        <span className="text-[12px] text-grey-secondary">
          {connection.updated_at
            ? `Saved ${when(connection.updated_at)} by ${savedBy(connection)}`
            : state === "environment"
              ? "Set up in the server's settings."
              : ""}
        </span>
        <Pill variant={connection.configured ? "ghost" : "primary"} size="sm" onClick={onEdit}>
          {connection.configured ? "Change" : "Set up"}
        </Pill>
      </div>
    </section>
  );
}

function ConnectionDialog({
  connection,
  onClose,
}: {
  connection: ConnectionResponse;
  onClose: () => void;
}) {
  const queryClient = useQueryClient();
  const [form, setForm] = useState<ConnectionForm>(() => formFromConnection(connection));
  const [result, setResult] = useState<ConnectionTestResponse | null>(null);

  const refresh = () => {
    void queryClient.invalidateQueries({ queryKey: CONNECTIONS_KEY });
    // The data sources view and the report set-up both read which systems are on.
    void queryClient.invalidateQueries({ queryKey: ["admin", "sync-status"] });
    void queryClient.invalidateQueries({ queryKey: ["day-reports", "setup"] });
  };
  const save = useMutation({
    mutationFn: () => apiClient.saveConnection(connection.connector, savePayload(connection, form)),
    onSuccess: (saved) => {
      toast.success(`${connection.name} saved${saved.enabled ? " and on" : ", off for now"}.`);
      refresh();
      onClose();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const testStop = testBlock(connection, form);
  const runTest = useMutation({
    mutationFn: () => apiClient.testConnection(connection.connector, testPayload(connection, form)),
    onSuccess: (response) => {
      setResult(response);
      if (response.recorded) refresh();
    },
    onError: (error) => toast.error(errorText(error)),
  });
  const remove = useMutation({
    mutationFn: () => apiClient.removeConnection(connection.connector),
    onSuccess: () => {
      toast.success(`${connection.name} removed, with every stored secret.`);
      refresh();
      onClose();
    },
    onError: (error) => toast.error(errorText(error)),
  });

  const fields = visibleFields(connection.fields, form.values);
  const missing = missingRequired(connection, form);
  const blocked = form.enabled && missing.length > 0;
  const edit = (next: ConnectionForm) => {
    setForm(next);
    // A result is about the values it tested; once they change it no longer says anything.
    setResult(null);
  };
  const setValue = (key: string, value: string) =>
    edit({
      ...form,
      values: withValidOptions(connection.fields, { ...form.values, [key]: value }),
    });

  return (
    <AdminDialog
      open
      onOpenChange={(open) => (open ? undefined : onClose())}
      title={connection.name}
      description={connection.description}
    >
      <form
        className="mt-4 grid gap-4"
        autoComplete="off"
        onSubmit={(event) => {
          event.preventDefault();
          if (!blocked) save.mutate();
        }}
      >
        {runsOnServerSettings(connection) ? (
          <p role="note" className="rounded-2xl bg-rag-amber-bg px-4 py-3 text-[13px] text-ink">
            {connection.name} runs on the server&apos;s own settings today. A connection saved here
            replaces them for this tenant until you remove it, so type the address you mean.
          </p>
        ) : null}
        <label className="flex items-center gap-3 rounded-2xl bg-grey-fill px-4 py-3 text-[14px] font-bold">
          <input
            type="checkbox"
            className="h-4 w-4 accent-[var(--op-magenta)]"
            checked={form.enabled}
            onChange={(event) => edit({ ...form, enabled: event.target.checked })}
          />
          On: use this connection for {connection.name}
        </label>

        {fields.map((field) => (
          <ConnectionField
            key={field.key}
            field={field}
            form={form}
            stored={connection.secrets_set.includes(field.key)}
            suggestions={result?.suggestions[field.key] ?? []}
            onValue={(value) => setValue(field.key, value)}
            onSecret={(value) =>
              edit({
                ...form,
                secrets: { ...form.secrets, [field.key]: value },
                clearedSecrets: form.clearedSecrets.filter((key) => key !== field.key),
              })
            }
            onClear={() =>
              edit({
                ...form,
                secrets: { ...form.secrets, [field.key]: "" },
                clearedSecrets: [...form.clearedSecrets, field.key],
              })
            }
            onKeep={() =>
              edit({
                ...form,
                clearedSecrets: form.clearedSecrets.filter((key) => key !== field.key),
              })
            }
          />
        ))}

        {result ? <TestResult result={result} /> : null}
        {blocked ? (
          <p className="text-[13px] text-rag-amber">
            Fill in {missing.map((field) => field.label).join(", ")} to turn it on, or switch it off
            to save it as a draft.
          </p>
        ) : null}
        {testStop ? <p className="text-[13px] text-grey-body">{testStop}</p> : null}

        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          <div>
            {connection.configured ? (
              <ConfirmDialog
                trigger={
                  <button type="button" className="text-[13px] font-bold text-rag-red">
                    Remove connection
                  </button>
                }
                title={`Remove ${connection.name}?`}
                description="Its settings and every stored secret are deleted. The server's own settings apply again, if it has any."
                confirmLabel="Remove"
                onConfirm={() => remove.mutate()}
              />
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            <Pill
              type="button"
              variant="ghost"
              size="sm"
              disabled={runTest.isPending || testStop !== null}
              title={testStop ?? undefined}
              onClick={() => runTest.mutate()}
            >
              {runTest.isPending ? "Testing…" : "Test connection"}
            </Pill>
            <Pill type="button" variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Pill>
            <Pill type="submit" size="sm" disabled={blocked || save.isPending}>
              {save.isPending ? "Saving…" : "Save"}
            </Pill>
          </div>
        </div>
        {connection.connector === "teams" ? (
          <p className={hintClass}>Testing Teams posts one short test message to the channel.</p>
        ) : connection.connector === "email" ? (
          <p className={hintClass}>Testing email signs in to the mail server; it sends nothing.</p>
        ) : null}
      </form>
    </AdminDialog>
  );
}

function ConnectionField({
  field,
  form,
  stored,
  suggestions,
  onValue,
  onSecret,
  onClear,
  onKeep,
}: {
  field: ConnectorFieldDto;
  form: ConnectionForm;
  stored: boolean;
  suggestions: { value: string; label: string }[];
  onValue: (value: string) => void;
  onSecret: (value: string) => void;
  onClear: () => void;
  onKeep: () => void;
}) {
  const id = `connection-${field.key}`;
  const label = field.required ? field.label : `${field.label} (optional)`;
  const cleared = form.clearedSecrets.includes(field.key);
  let control: ReactNode;
  if (field.kind === "select") {
    control = (
      <select
        id={id}
        className={inputClass}
        value={form.values[field.key] ?? ""}
        onChange={(event) => onValue(event.target.value)}
      >
        {visibleOptions(field, form.values).map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    );
  } else if (field.kind === "secret") {
    control = (
      <div className="flex items-center gap-2">
        {/* Never filled from the server: the API does not return secrets. */}
        <input
          id={id}
          type="password"
          autoComplete="new-password"
          className={inputClass}
          value={form.secrets[field.key] ?? ""}
          placeholder={
            stored && !cleared
              ? "Stored. Type a new one to replace it."
              : field.placeholder || "Not set"
          }
          onChange={(event) => onSecret(event.target.value)}
        />
        {stored && !cleared ? (
          <Pill type="button" variant="ghost" size="sm" onClick={onClear}>
            Clear
          </Pill>
        ) : null}
        {stored && cleared ? (
          <Pill type="button" variant="ghost" size="sm" onClick={onKeep}>
            Keep
          </Pill>
        ) : null}
      </div>
    );
  } else {
    control = (
      <input
        id={id}
        className={inputClass}
        inputMode={field.kind === "number" ? "numeric" : field.kind === "url" ? "url" : undefined}
        type={field.kind === "email" ? "email" : "text"}
        value={form.values[field.key] ?? ""}
        placeholder={field.placeholder}
        onChange={(event) => onValue(event.target.value)}
      />
    );
  }
  return (
    <div>
      <label htmlFor={id} className={labelClass}>
        {label}
      </label>
      {control}
      {field.kind === "secret" && stored ? (
        <p className={cn(hintClass, "flex items-center gap-1")}>
          <KeyRound size={12} aria-hidden />
          {cleared ? "The stored value is deleted when you save." : "A value is stored."}
        </p>
      ) : null}
      {suggestions.length > 0 ? (
        <select
          aria-label={`Pick ${field.label}`}
          className={cn(inputClass, "mt-2")}
          value=""
          onChange={(event) => event.target.value && onValue(event.target.value)}
        >
          <option value="">Pick one of the fields the system reported…</option>
          {suggestions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      ) : null}
      {field.help ? <p className={hintClass}>{field.help}</p> : null}
    </div>
  );
}

function TestResult({ result }: { result: ConnectionTestResponse }) {
  return (
    <div
      role="status"
      className={cn(
        "rounded-2xl px-4 py-3 text-[13px]",
        result.ok ? "bg-rag-green-bg text-rag-green" : "bg-rag-red-bg text-rag-red",
      )}
    >
      <p className="flex items-center gap-2 font-bold">
        {result.ok ? <CircleCheck size={14} aria-hidden /> : <CircleAlert size={14} aria-hidden />}
        {result.message}
      </p>
      {result.details.length > 0 ? (
        <dl className="mt-2 grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-ink">
          {result.details.map((detail) => (
            <div key={detail.label} className="contents">
              <dt className="text-grey-secondary">{detail.label}</dt>
              <dd className="break-words">{detail.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {!result.recorded ? (
        <p className="mt-1 text-[12px] text-grey-secondary">
          Tested with the values above; save to keep them.
        </p>
      ) : null}
    </div>
  );
}
