import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { apiClient } from "../../api/client";
import type { SyncSourceStatusResponse, SyncTargetStatusResponse } from "../../api/schema";
import { useRole } from "../../app/role";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import type { BadgeTone } from "../../lib/status";
import {
  cronLabel,
  errorText,
  minutesLabel,
  nextCronRun,
  peopleCount,
  providerLabel,
  stampLabel as when,
  targetParts,
} from "./adminWords";

const STATUS_KEY = ["admin", "sync-status"] as const;

const SOURCE_WORDS: Record<SyncSourceStatusResponse["source"], string> = {
  issue_tracker: "Issue tracker",
  vcs: "Version control",
  calendar: "Calendar",
  directory: "Chat directory",
};

const HEALTH: Record<SyncSourceStatusResponse["health"], { tone: BadgeTone; word: string }> = {
  healthy: { tone: "success", word: "healthy" },
  stale: { tone: "warning", word: "stale" },
  failing: { tone: "danger", word: "failing" },
  never_synced: { tone: "neutral", word: "never synced" },
  not_configured: { tone: "neutral", word: "not configured" },
  disabled: { tone: "neutral", word: "switched off" },
};

const ORIGIN_WORDS: Record<SyncSourceStatusResponse["target_origin"], string | null> = {
  runtime_config: "What it reads comes from each project's and pod's settings.",
  environment: "What it reads comes from the server's settings.",
  workspace: null,
  none: null,
};

/** The source's own health word; "never synced" only when nothing has synced at all. */
function sourceHealth(source: SyncSourceStatusResponse) {
  if (source.source === "calendar" && source.health === "disabled") {
    return { tone: "neutral" as BadgeTone, word: "read live" };
  }
  if (source.health === "never_synced" && source.last_synced_at) {
    return { tone: "neutral" as BadgeTone, word: "some never synced" };
  }
  return HEALTH[source.health];
}

/**
 * Where delivery facts come from, how fresh each source is, and what went
 * wrong last. Jira and Git run on their schedule only: the API has no way yet
 * to start the scheduled run on request, so the next run is shown instead. The
 * chat directory can be synced now; that endpoint runs the scheduled sync.
 */
export function DataSourcesTab() {
  const { chatEnabled } = useRole();
  const status = useQuery({ queryKey: STATUS_KEY, queryFn: () => apiClient.syncStatus() });
  const sources = status.data?.sources ?? [];

  return (
    <PanelState
      isLoading={status.isLoading}
      error={status.error}
      onRetry={() => void status.refetch()}
      isEmpty={sources.length === 0}
      emptyText="No data sources are configured."
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        <p className="text-[13px] text-grey-body">
          Read {when(status.data?.generated_at)}. Each source syncs on its own schedule
          {chatEnabled ? "; a check-in for one person is asked from the Chat screen" : ""}.
        </p>
        {sources.map((source) => (
          <SourcePanel
            key={source.source}
            source={source}
            readAt={status.data?.generated_at ?? null}
          />
        ))}
      </div>
    </PanelState>
  );
}

function SourcePanel({
  source,
  readAt,
}: {
  source: SyncSourceStatusResponse;
  readAt: string | null;
}) {
  const health = sourceHealth(source);
  const next = source.sync_enabled
    ? nextCronRun(source.schedule, readAt ? new Date(readAt) : new Date())
    : null;
  const error = source.config_error ?? source.provider_error ?? source.last_error;
  const origin = ORIGIN_WORDS[source.target_origin];

  return (
    <Panel
      title={`${SOURCE_WORDS[source.source]} · ${providerLabel(source.provider, source.simulated)}`}
      note={
        <RagChip tone={health.tone} className="h-6 px-2.5 text-[12px]">
          {health.word}
        </RagChip>
      }
    >
      {source.sync_enabled ? (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 text-[13px] text-grey-body">
            <p>
              Syncs {cronLabel(source.schedule)}
              {next ? (
                <>
                  {" "}
                  · <b>next run {when(next.toISOString())}</b>
                </>
              ) : null}
              . Last synced {when(source.last_synced_at)} · last tried{" "}
              {when(source.last_attempt_at)}
              {source.newest_item_at ? ` · newest item ${when(source.newest_item_at)}` : ""}.
            </p>
            <p className="mt-1 text-[12px] text-grey-secondary">
              {origin ? `${origin} ` : ""}
              {source.stale_after_minutes
                ? `Counted as stale after ${minutesLabel(source.stale_after_minutes * 60)} without a sync.`
                : ""}
              {source.source === "directory"
                ? ""
                : " A run can't be started by hand yet; the next scheduled run picks up every change."}
            </p>
          </div>
          {source.source === "directory" ? <DirectorySyncNow /> : null}
        </div>
      ) : (
        <p className="text-[13px] text-grey-body">
          {source.source === "calendar"
            ? "Nothing to sync: OpenProgram reads each person's calendar just before their check-in reminder goes out, so leave is always current."
            : "Not synced on a schedule."}
        </p>
      )}
      {error ? (
        <p className="mt-2 rounded-2xl bg-rag-red-bg px-3 py-2 text-[13px] text-rag-red">{error}</p>
      ) : null}
      {source.targets.length > 0 ? (
        <div className="mt-3">
          <TableBox>
            <table className="w-full min-w-[600px] border-collapse">
              <thead>
                <tr>
                  <th className={th}>What it reads</th>
                  <th className={th}>Health</th>
                  <th className={th}>Last synced</th>
                  <th className={`${th} text-right`}>Items last run</th>
                </tr>
              </thead>
              <tbody>
                {source.targets.map((target) => (
                  <TargetRow key={target.scope} target={target} />
                ))}
              </tbody>
            </table>
          </TableBox>
        </div>
      ) : null}
    </Panel>
  );
}

function TargetRow({ target }: { target: SyncTargetStatusResponse }) {
  const parts = targetParts(target);
  const line = [
    parts.kind,
    parts.detail,
    target.configured ? null : "no longer configured, kept for its history",
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <tr>
      <td className={td}>
        <span className="font-bold break-words">{parts.name}</span>
        {line ? (
          <span className="block break-words text-[11px] text-grey-secondary">{line}</span>
        ) : null}
        {target.last_error ? (
          <span className="block text-[12px] text-rag-red">{target.last_error}</span>
        ) : null}
      </td>
      <td className={td}>
        <RagChip tone={HEALTH[target.health].tone} className="h-6 px-2.5 text-[12px]">
          {HEALTH[target.health].word}
        </RagChip>
      </td>
      <td className={td}>{when(target.last_synced_at)}</td>
      <td className={`${td} text-right tabular-nums`}>{target.items_synced ?? "—"}</td>
    </tr>
  );
}

/** The scheduled directory sync, run now: it records its outcome like a scheduled run. */
function DirectorySyncNow() {
  const queryClient = useQueryClient();
  const sync = useMutation({
    mutationFn: () => apiClient.syncDirectory(),
    onSuccess: (result) => {
      const left = result.deactivated_count
        ? `; ${peopleCount(result.deactivated_count)} no longer there were marked as left`
        : "";
      toast.success(`Synced ${peopleCount(result.synced_count)} from the chat directory${left}.`);
    },
    onError: (error) => toast.error(`The chat directory could not be synced: ${errorText(error)}`),
    // Success and failure are both recorded, so the status is read again either way.
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: STATUS_KEY });
      void queryClient.invalidateQueries({ queryKey: ["config", "directory"] });
    },
  });
  return (
    <Pill size="sm" variant="ghost" disabled={sync.isPending} onClick={() => sync.mutate()}>
      {sync.isPending ? "Syncing…" : "Sync now"}
    </Pill>
  );
}
