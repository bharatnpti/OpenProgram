import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import type { SyncSourceStatusResponse } from "../../api/schema";
import { PanelState, TableBox, td, th } from "../../components/PanelState";
import { Panel } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay, formatTime } from "../../lib/format";
import type { BadgeTone } from "../../lib/status";

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

const when = (iso: string | null | undefined) =>
  iso ? `${formatDay(iso)} ${formatTime(iso)}` : "—";

/** Where delivery facts come from, how fresh each source is, and what went wrong last. */
export function DataSourcesTab() {
  const status = useQuery({
    queryKey: ["admin", "sync-status"],
    queryFn: () => apiClient.syncStatus(),
  });

  return (
    <PanelState
      needs="an admin"
      isLoading={status.isLoading}
      error={status.error}
      onRetry={() => void status.refetch()}
      isEmpty={(status.data?.sources ?? []).length === 0}
      emptyText="No data sources are configured."
    >
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        <p className="text-[13px] text-grey-body">
          Read {when(status.data?.generated_at)}. Syncs run on their schedule; a manual check-in for
          one person is on the Chat screen.
        </p>
        {(status.data?.sources ?? []).map((s) => (
          <Panel
            key={s.source}
            title={`${SOURCE_WORDS[s.source]} · ${s.provider}${s.simulated ? " (simulated)" : ""}`}
            note={
              <RagChip tone={HEALTH[s.health].tone} className="h-6 px-2.5 text-[12px]">
                {HEALTH[s.health].word}
              </RagChip>
            }
          >
            <p className="text-[13px] text-grey-body">
              Last synced {when(s.last_synced_at)} · last tried {when(s.last_attempt_at)}
              {s.schedule ? ` · schedule ${s.schedule}` : ""}
              {s.newest_item_at ? ` · newest item ${when(s.newest_item_at)}` : ""}
            </p>
            {s.last_error || s.config_error || s.provider_error ? (
              <p className="mt-2 rounded-2xl bg-rag-red-bg px-3 py-2 text-[13px] text-rag-red">
                {s.config_error ?? s.provider_error ?? s.last_error}
              </p>
            ) : null}
            {s.targets.length > 0 ? (
              <div className="mt-3">
                <TableBox>
                  <table className="w-full min-w-[600px] border-collapse">
                    <thead>
                      <tr>
                        <th className={th}>Target</th>
                        <th className={th}>Health</th>
                        <th className={th}>Last synced</th>
                        <th className={`${th} text-right`}>Items</th>
                      </tr>
                    </thead>
                    <tbody>
                      {s.targets.map((t) => (
                        <tr key={`${t.scope}-${t.label}`}>
                          <td className={td}>
                            <span className="font-bold">{t.label}</span>
                            <span className="block text-[11px] text-grey-secondary">
                              {t.scope}
                              {t.detail ? ` · ${t.detail}` : ""}
                            </span>
                            {t.last_error ? (
                              <span className="block text-[12px] text-rag-red">{t.last_error}</span>
                            ) : null}
                          </td>
                          <td className={td}>
                            <RagChip
                              tone={HEALTH[t.health].tone}
                              className="h-6 px-2.5 text-[12px]"
                            >
                              {HEALTH[t.health].word}
                            </RagChip>
                          </td>
                          <td className={td}>{when(t.last_synced_at)}</td>
                          <td className={`${td} text-right tabular-nums`}>
                            {t.items_synced ?? "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </TableBox>
              </div>
            ) : null}
          </Panel>
        ))}
      </div>
    </PanelState>
  );
}
