import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleAlert, CircleCheck, KeyRound, PlugZap, Settings2, Trash2 } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type {
  ConnectionResponse,
  ConnectionTestResponse,
  ConnectorFieldDto,
  ConnectorPurpose,
} from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { TextInput } from "../../components/ui/Field";
import { Modal } from "../../components/ui/Modal";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import type { BadgeTone } from "../../lib/status";
import { cn } from "../../lib/utils";
import { AdminSelect } from "./AdminSelect";
import { ConfirmDialog } from "./ConfirmDialog";
import { FormField } from "./FormField";
import { errorMessage } from "./adminTypes";
import {
  type ConnectionForm,
  type ConnectionState,
  connectionState,
  formFromConnection,
  missingRequired,
  savePayload,
  savedBy,
  testPayload,
  visibleFields,
  visibleOptions,
  withValidOptions,
} from "./connectionForm";

export const CONNECTIONS_QUERY_KEY = ["config", "integrations"] as const;

const PURPOSE_GROUPS: { purpose: ConnectorPurpose; title: string; note?: string }[] = [
  { purpose: "issue_tracker", title: "Issue tracker" },
  { purpose: "code", title: "Code", note: "One code host at a time." },
  { purpose: "chat", title: "Chat" },
  { purpose: "report_delivery", title: "Day report delivery" },
  { purpose: "calendar", title: "Calendar" },
];

const STATE_LABELS: Record<ConnectionState, string> = {
  on: "On",
  off: "Off",
  environment: "Server settings",
  not_set_up: "Not set up",
};

const STATE_TONES: Record<ConnectionState, BadgeTone> = {
  on: "success",
  off: "neutral",
  environment: "info",
  not_set_up: "neutral",
};

/**
 * The systems OpenProgram connects to, set up per tenant.
 *
 * Secrets go in and never come back: a stored one shows as "stored" and is
 * kept unless it is typed over or cleared. A connector the tenant has not
 * turned on falls back to the server's own settings, and the card says so.
 */
export function IntegrationsPanel() {
  const connections = useQuery({ queryKey: CONNECTIONS_QUERY_KEY, queryFn: apiClient.connections });
  const [editing, setEditing] = useState<string | null>(null);
  const editingConnection = connections.data?.find((item) => item.connector === editing) ?? null;

  return (
    <div className="flex flex-col gap-5">
      <div>
        <h2 className="text-[18px] font-bold">Integrations</h2>
        <p className="mt-1 max-w-[680px] text-[13px] text-grey-secondary">
          Connect the systems OpenProgram reads from and sends to. Tokens and passwords are stored
          encrypted and are never shown again. A system that is not turned on here uses the
          server&apos;s own settings, if it has any.
        </p>
      </div>

      {connections.isError ? (
        <p className="text-[14px] text-rag-red">{errorMessage(connections.error)}</p>
      ) : !connections.data ? (
        <Card padding="px-5 py-10" className="text-center text-[14px] text-grey-secondary">
          Loading integrations…
        </Card>
      ) : (
        PURPOSE_GROUPS.map((group) => {
          const items = connections.data.filter((item) => item.purposes[0] === group.purpose);
          if (items.length === 0) return null;
          return (
            <section key={group.purpose} className="flex flex-col gap-3">
              <div className="flex items-baseline gap-2">
                <h3 className="text-[15px] font-bold">{group.title}</h3>
                {group.note ? (
                  <span className="text-[12px] text-grey-secondary">{group.note}</span>
                ) : null}
              </div>
              <div className="grid gap-3 lg:grid-cols-2">
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
        })
      )}

      {editingConnection ? (
        <ConnectionDialog
          key={editingConnection.connector}
          connection={editingConnection}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </div>
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
  return (
    <Card padding="p-5" className="flex flex-col gap-3">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[16px] font-bold">{connection.name}</div>
          <p className="mt-0.5 text-[13px] text-grey-secondary">{connection.description}</p>
        </div>
        <RagChip tone={STATE_TONES[state]} dot>
          {STATE_LABELS[state]}
        </RagChip>
      </div>
      {test ? (
        <p className={cn("text-[13px]", test.ok ? "text-rag-green" : "text-rag-red")}>
          {test.ok ? "Last test passed" : "Last test failed"}: {test.message}{" "}
          <span className="text-grey-secondary">· {formatWhen(test.tested_at)}</span>
        </p>
      ) : state === "on" ? (
        <p className="text-[13px] text-grey-secondary">Not tested since it was last saved.</p>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-[12px] text-grey-secondary">
          {connection.updated_at
            ? `Saved ${formatWhen(connection.updated_at)} by ${savedBy(connection)}`
            : state === "environment"
              ? "Set up in the server settings."
              : ""}
        </span>
        <Pill variant={connection.configured ? "ghost" : "primary"} size="sm" onClick={onEdit}>
          <Settings2 size={14} />
          {connection.configured ? "Edit" : "Set up"}
        </Pill>
      </div>
    </Card>
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
  const [confirmRemove, setConfirmRemove] = useState(false);

  useEffect(() => setResult(null), [form.values, form.secrets]);

  const refresh = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: CONNECTIONS_QUERY_KEY }),
      queryClient.invalidateQueries({ queryKey: ["admin", "sync-status"] }),
      queryClient.invalidateQueries({ queryKey: ["config", "report-destinations"] }),
    ]);
  };
  const save = useMutation({
    mutationFn: () => apiClient.saveConnection(connection.connector, savePayload(connection, form)),
    onSuccess: async () => {
      await refresh();
      toast.success(`${connection.name} saved.`);
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const runTest = useMutation({
    mutationFn: () => apiClient.testConnection(connection.connector, testPayload(connection, form)),
    onSuccess: async (response) => {
      setResult(response);
      if (response.recorded) await refresh();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const remove = useMutation({
    mutationFn: () => apiClient.removeConnection(connection.connector),
    onSuccess: async () => {
      await refresh();
      toast.success(`${connection.name} removed.`);
      onClose();
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const fields = visibleFields(connection.fields, form.values);
  const missing = missingRequired(connection, form);
  const blocked = form.enabled && missing.length > 0;
  const setValue = (key: string, value: string) =>
    setForm((current) => ({
      ...current,
      values: withValidOptions(connection.fields, { ...current.values, [key]: value }),
    }));

  return (
    <Modal open onOpenChange={(open) => (open ? undefined : onClose())} title={connection.name}>
      <form
        className="flex max-h-[70vh] flex-col gap-4 overflow-y-auto pr-1"
        onSubmit={(event) => {
          event.preventDefault();
          if (!blocked) save.mutate();
        }}
      >
        <label className="flex items-center gap-3 rounded-2xl bg-grey-fill px-4 py-3 text-[14px] font-bold">
          <input
            type="checkbox"
            className="h-4 w-4 accent-[var(--op-magenta)]"
            checked={form.enabled}
            onChange={(event) => setForm({ ...form, enabled: event.target.checked })}
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
              setForm((current) => ({
                ...current,
                secrets: { ...current.secrets, [field.key]: value },
                clearedSecrets: current.clearedSecrets.filter((key) => key !== field.key),
              }))
            }
            onClear={() =>
              setForm((current) => ({
                ...current,
                secrets: { ...current.secrets, [field.key]: "" },
                clearedSecrets: [...current.clearedSecrets, field.key],
              }))
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

        <div className="flex flex-wrap items-center justify-between gap-2 pt-1">
          <div>
            {connection.configured ? (
              <Pill variant="ghost" size="sm" onClick={() => setConfirmRemove(true)}>
                <Trash2 size={14} />
                Remove
              </Pill>
            ) : null}
          </div>
          <div className="flex flex-wrap gap-2">
            <Pill
              variant="ghost"
              size="sm"
              disabled={runTest.isPending}
              onClick={() => runTest.mutate()}
            >
              <PlugZap size={14} />
              {runTest.isPending ? "Testing…" : "Test connection"}
            </Pill>
            <Pill variant="ghost" size="sm" onClick={onClose}>
              Cancel
            </Pill>
            <Pill type="submit" size="sm" disabled={blocked || save.isPending}>
              {save.isPending ? "Saving…" : "Save"}
            </Pill>
          </div>
        </div>
        {connection.connector === "teams" ? (
          <p className="text-[12px] text-grey-secondary">
            Testing Teams posts one short test message to the channel.
          </p>
        ) : null}
      </form>

      <ConfirmDialog
        state={
          confirmRemove
            ? {
                open: true,
                title: `Remove ${connection.name}?`,
                description:
                  "Its settings and every stored secret are deleted. The server's own settings apply again, if it has any.",
                confirmLabel: "Remove",
                destructive: true,
                onConfirm: () => remove.mutate(),
              }
            : { open: false }
        }
        onOpenChange={setConfirmRemove}
      />
    </Modal>
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
}: {
  field: ConnectorFieldDto;
  form: ConnectionForm;
  stored: boolean;
  suggestions: { value: string; label: string }[];
  onValue: (value: string) => void;
  onSecret: (value: string) => void;
  onClear: () => void;
}) {
  const id = `connection-${field.key}`;
  const label = field.required ? field.label : `${field.label} (optional)`;
  let control: ReactNode;
  if (field.kind === "select") {
    control = (
      <AdminSelect
        id={id}
        value={form.values[field.key] ?? ""}
        onChange={(e) => onValue(e.target.value)}
      >
        {visibleOptions(field, form.values).map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </AdminSelect>
    );
  } else if (field.kind === "secret") {
    const cleared = form.clearedSecrets.includes(field.key);
    control = (
      <div className="flex items-center gap-2">
        <TextInput
          id={id}
          type="password"
          autoComplete="new-password"
          value={form.secrets[field.key] ?? ""}
          placeholder={
            stored && !cleared ? "Stored. Type a new one to replace it." : field.placeholder
          }
          onChange={(event) => onSecret(event.target.value)}
        />
        {stored && !cleared ? (
          <Pill variant="ghost" size="sm" onClick={onClear} title="Delete the stored value on save">
            Clear
          </Pill>
        ) : null}
      </div>
    );
  } else {
    control = (
      <TextInput
        id={id}
        inputMode={field.kind === "number" ? "numeric" : field.kind === "url" ? "url" : undefined}
        type={field.kind === "email" ? "email" : "text"}
        value={form.values[field.key] ?? ""}
        placeholder={field.placeholder}
        onChange={(event) => onValue(event.target.value)}
      />
    );
  }
  return (
    <FormField label={label} htmlFor={id}>
      {control}
      {field.kind === "secret" && stored && !form.clearedSecrets.includes(field.key) ? (
        <p className="mt-1 flex items-center gap-1 text-[12px] text-grey-secondary">
          <KeyRound size={12} /> A value is stored.
        </p>
      ) : null}
      {suggestions.length > 0 ? (
        <AdminSelect
          aria-label={`Pick ${field.label}`}
          className="mt-2"
          value=""
          onChange={(event) => event.target.value && onValue(event.target.value)}
        >
          <option value="">Pick one of the fields the system reported…</option>
          {suggestions.map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </AdminSelect>
      ) : null}
      {field.help ? <p className="mt-1 text-[12px] text-grey-secondary">{field.help}</p> : null}
    </FormField>
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
      <div className="flex items-center gap-2 font-bold">
        {result.ok ? <CircleCheck size={14} /> : <CircleAlert size={14} />}
        {result.message}
      </div>
      {result.details.length > 0 ? (
        <dl className="mt-2 grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5 text-ink">
          {result.details.map((detail) => (
            <div key={detail.label} className="contents">
              <dt className="text-grey-secondary">{detail.label}</dt>
              <dd>{detail.value}</dd>
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

function formatWhen(iso: string): string {
  const date = new Date(iso);
  return date.toLocaleString(undefined, {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  });
}
