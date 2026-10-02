import { useQuery } from "@tanstack/react-query";
import { CircleAlert, DatabaseZap, Info, RefreshCw } from "lucide-react";
import type { ReactNode } from "react";

import { apiClient } from "../../api/client";
import type {
  SyncHealth,
  SyncSource,
  SyncSourceStatusResponse,
  SyncTargetOrigin,
  SyncTargetStatusResponse,
} from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import type { BadgeTone } from "../../lib/status";
import { cn } from "../../lib/utils";
import { errorMessage } from "./adminTypes";

const SOURCE_LABELS: Record<SyncSource, string> = {
  issue_tracker: "Issue tracker",
  vcs: "Code repositories",
  calendar: "Calendar",
  directory: "Chat directory",
};

const PROVIDER_LABELS: Record<string, string> = {
  jira: "Jira",
  github: "GitHub",
  gitlab: "GitLab",
  google: "Google Calendar",
  slack: "Slack",
  mock_slack: "Built-in chat",
  fake: "Built-in sample provider",
};

const HEALTH_LABELS: Record<SyncHealth, string> = {
  healthy: "Up to date",
  stale: "Stale",
  failing: "Failing",
  never_synced: "Never synced",
  not_configured: "Not configured",
  disabled: "Not synced",
};

const HEALTH_TONES: Record<SyncHealth, BadgeTone> = {
  healthy: "success",
  stale: "warning",
  failing: "danger",
  never_synced: "warning",
  not_configured: "neutral",
  disabled: "neutral",
};

const ORIGIN_LABELS: Record<SyncTargetOrigin, string> = {
  runtime_config: "Targets come from project and pod settings in Entities.",
  environment: "Targets come from the server's environment sync lists.",
  workspace: "Syncs the whole chat workspace.",
  none: "",
};

/**
 * Whether each hard-signal source is actually syncing.
 *
 * Read-only: every number comes from what sync runs recorded, so a source that
 * never ran says so instead of looking healthy. The only action it links to is
 * the existing directory sync on the Directory tab.
 */
export function DataSourcesPanel({ onOpenDirectory }: { onOpenDirectory: () => void }) {
  const status = useQuery({
    queryKey: ["admin", "sync-status"],
    queryFn: apiClient.syncStatus,
    refetchInterval: 60_000,
  });
  const now = status.data ? Date.parse(status.data.generated_at) : Date.now();

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-[18px] font-bold">Data sources</h2>
          <p className="mt-1 max-w-[640px] text-[13px] text-grey-secondary">
            Where hard signals come from, when each source last synced, and whether its last run
            worked. Recurring syncs run on the worker&apos;s schedule.
          </p>
        </div>
        <Pill
          variant="ghost"
          size="sm"
          disabled={status.isFetching}
          onClick={() => void status.refetch()}
        >
          <RefreshCw size={14} />
          {status.isFetching ? "Checking…" : "Refresh"}
        </Pill>
      </div>

      {status.isError ? (
        <p className="text-[14px] text-rag-red">{errorMessage(status.error)}</p>
      ) : !status.data ? (
        <div className="rounded-3xl border border-grey-border bg-white px-5 py-10 text-center text-[14px] text-grey-secondary">
          Loading sync status…
        </div>
      ) : (
        status.data.sources.map((source) => (
          <SourceCard
            key={source.source}
            source={source}
            now={now}
            onOpenDirectory={onOpenDirectory}
          />
        ))
      )}
    </div>
  );
}

function SourceCard({
  source,
  now,
  onOpenDirectory,
}: {
  source: SyncSourceStatusResponse;
  now: number;
  onOpenDirectory: () => void;
}) {
  const warning = sourceWarning(source, now);
  const showsItems = source.source === "issue_tracker" || source.source === "vcs";
  const origin = ORIGIN_LABELS[source.target_origin];

  return (
    <Card padding="p-5" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-[16px] font-bold">{SOURCE_LABELS[source.source]}</h3>
          <div className="mt-0.5 text-[13px] text-grey-secondary">
            {providerLabel(source.provider)}
            {source.simulated ? " · sample data, not a live system" : ""}
          </div>
        </div>
        <RagChip tone={HEALTH_TONES[source.health]} dot>
          {HEALTH_LABELS[source.health]}
        </RagChip>
      </div>

      {source.sync_enabled ? (
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <Fact label="Last synced" iso={source.last_synced_at} now={now} empty="Never" />
          <Fact label="Last attempt" iso={source.last_attempt_at} now={now} empty="None recorded" />
          {showsItems ? (
            <Fact
              label="Newest item seen"
              iso={source.newest_item_at}
              now={now}
              empty="No items yet"
            />
          ) : null}
          <div>
            <dt className="text-[12px] font-bold uppercase tracking-wide text-grey-secondary">
              Schedule
            </dt>
            <dd className="mt-0.5 text-[14px]">
              {source.schedule ? (
                <code className="font-mono text-[13px]">{source.schedule}</code>
              ) : (
                "—"
              )}
              {source.stale_after_minutes ? (
                <div className="text-[12px] text-grey-secondary">
                  Warns after {formatDuration(source.stale_after_minutes)} without a sync
                </div>
              ) : null}
            </dd>
          </div>
        </dl>
      ) : (
        <Note tone="neutral" icon={<Info size={14} />}>
          Not synced by design. Availability is read live from the calendar before a check-in
          reminder goes out, so there is nothing stored to go stale.
        </Note>
      )}

      {warning ? (
        <Note
          tone={source.health === "failing" ? "danger" : "warning"}
          icon={<CircleAlert size={14} />}
        >
          {warning}
        </Note>
      ) : null}

      {source.targets.length > 0 ? <TargetTable targets={source.targets} now={now} /> : null}

      {origin || source.source === "directory" ? (
        <div className="flex flex-wrap items-center justify-between gap-3">
          <p className="text-[13px] text-grey-secondary">{origin}</p>
          {source.source === "directory" ? (
            <Pill variant="ghost" size="sm" onClick={onOpenDirectory}>
              <DatabaseZap size={14} />
              Run a directory sync
            </Pill>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}

function TargetTable({ targets, now }: { targets: SyncTargetStatusResponse[]; now: number }) {
  return (
    <div className="overflow-hidden rounded-2xl border border-grey-border">
      <div className="hidden grid-cols-[1fr_110px_110px_60px_130px] gap-4 bg-grey-header px-4 py-2.5 text-[12px] font-bold uppercase tracking-wide text-grey-secondary sm:grid">
        <span>Target</span>
        <span>Last synced</span>
        <span>Last attempt</span>
        <span className="text-right">Items</span>
        <span className="text-right">Status</span>
      </div>
      {targets.map((target, index) => (
        <div
          key={target.scope}
          className={cn(
            "grid gap-x-4 gap-y-1 px-4 py-3 sm:grid-cols-[1fr_110px_110px_60px_130px] sm:items-center",
            index > 0 && "border-t border-grey-fill",
          )}
        >
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="truncate text-[14px] font-bold">{target.label}</span>
              {!target.configured ? (
                <RagChip tone="neutral" className="h-6 px-2.5 text-[12px]">
                  Not in current config
                </RagChip>
              ) : null}
            </div>
            {target.detail ? (
              <div
                className="truncate font-mono text-[12px] text-grey-secondary"
                title={target.detail}
              >
                {target.detail}
              </div>
            ) : null}
            {target.last_error ? (
              <div className="text-[12px] text-rag-red">{target.last_error}</div>
            ) : null}
          </div>
          <TimeCell iso={target.last_synced_at} now={now} empty="Never" />
          <TimeCell iso={target.last_attempt_at} now={now} empty="—" />
          <span className="text-[13px] tabular-nums text-grey-secondary sm:text-right">
            {target.items_synced ?? "—"}
          </span>
          <span className="sm:text-right">
            <RagChip tone={HEALTH_TONES[target.health]} className="h-6 px-2.5 text-[12px]">
              {target.last_outcome === "failed" ? "Last run failed" : HEALTH_LABELS[target.health]}
            </RagChip>
          </span>
        </div>
      ))}
    </div>
  );
}

function Fact({
  label,
  iso,
  now,
  empty,
}: {
  label: string;
  iso: string | null | undefined;
  now: number;
  empty: string;
}) {
  return (
    <div>
      <dt className="text-[12px] font-bold uppercase tracking-wide text-grey-secondary">{label}</dt>
      <dd className="mt-0.5 text-[14px]" title={iso ? new Date(iso).toLocaleString() : undefined}>
        {formatAgo(iso, now) ?? <span className="text-grey-secondary">{empty}</span>}
      </dd>
    </div>
  );
}

function TimeCell({
  iso,
  now,
  empty,
}: {
  iso: string | null | undefined;
  now: number;
  empty: string;
}) {
  return (
    <span
      className="text-[13px] text-grey-secondary"
      title={iso ? new Date(iso).toLocaleString() : undefined}
    >
      {formatAgo(iso, now) ?? empty}
    </span>
  );
}

function Note({
  tone,
  icon,
  children,
}: {
  tone: "danger" | "warning" | "neutral";
  icon: ReactNode;
  children: ReactNode;
}) {
  return (
    <p
      className={cn(
        "flex items-start gap-2 rounded-2xl px-4 py-3 text-[13px]",
        tone === "danger" && "bg-rag-red-bg text-rag-red",
        tone === "warning" && "bg-rag-amber-bg text-rag-amber",
        tone === "neutral" && "bg-grey-fill text-grey-secondary",
      )}
    >
      <span className="mt-0.5 shrink-0">{icon}</span>
      <span>{children}</span>
    </p>
  );
}

/** The plain-language warning for a source, or null when there is nothing to flag. */
function sourceWarning(source: SyncSourceStatusResponse, now: number): string | null {
  const stale =
    source.source === "directory"
      ? "The member directory may be out of date."
      : "Signals from this source may be out of date.";
  switch (source.health) {
    case "failing":
      if (source.provider_error) {
        return `${source.provider_error}. Syncs from this source can't run until its credentials or settings are fixed. ${stale}`;
      }
      if (source.config_error) {
        return `Sync targets can't be resolved, so recurring syncs stop until the project or pod settings are fixed: ${source.config_error}`;
      }
      return `The last sync failed${agoSuffix(source.last_attempt_at, now)}. ${
        source.last_error ?? "Check the worker logs."
      } ${stale}`;
    case "stale": {
      const age = formatAgo(source.last_synced_at, now) ?? "a while ago";
      const limit = source.stale_after_minutes
        ? `, longer than the ${formatDuration(source.stale_after_minutes)} warning limit`
        : "";
      return `Last successful sync was ${age}${limit}. ${stale}`;
    }
    case "never_synced":
      return source.source === "directory"
        ? "The directory has not synced yet, so new workspace members can't be imported."
        : "Targets are configured but no sync has completed yet.";
    case "not_configured":
      if (source.source === "issue_tracker") {
        return "No recurring targets. Add a Jira project key or base JQL to a project in Entities.";
      }
      if (source.source === "vcs") {
        return "No recurring targets. Add repositories to a project in Entities.";
      }
      return null;
    default:
      return null;
  }
}

function providerLabel(provider: string): string {
  return PROVIDER_LABELS[provider] ?? provider;
}

function agoSuffix(iso: string | null | undefined, now: number): string {
  const ago = formatAgo(iso, now);
  return ago ? ` ${ago}` : "";
}

/** "12 min ago"; an absolute date for anything in the future (clock skew, seeded data). */
function formatAgo(iso: string | null | undefined, now: number): string | null {
  if (!iso) return null;
  const then = Date.parse(iso);
  if (Number.isNaN(then)) return null;
  const diffMinutes = Math.floor((now - then) / 60_000);
  if (diffMinutes < -1) return new Date(then).toLocaleString();
  if (diffMinutes < 1) return "just now";
  return `${formatDuration(diffMinutes)} ago`;
}

function formatDuration(minutes: number): string {
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.round(minutes / 60);
  if (hours < 48) return `${hours} h`;
  const days = Math.round(hours / 24);
  return `${days} day${days === 1 ? "" : "s"}`;
}
