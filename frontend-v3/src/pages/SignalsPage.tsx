import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";

import { apiClient } from "../api/client";
import type {
  DriftFindingResponse,
  PortfolioFlowResponse,
  RiskFindingResponse,
} from "../api/schema";
import { useNames } from "../app/directory";
import { useRole } from "../app/role";
import { PanelState, SectionHeader, TableBox, td, th } from "../components/PanelState";
import { ChipPicker, Panel, RagBadge, RagDot } from "../components/ui/Bits";
import { RagChip } from "../components/ui/RagChip";
import { formatDay, formatTime } from "../lib/format";
import { ragSeverity } from "../lib/status";
import { sourceLine } from "../lib/words";

const VIEWS = ["everything", "risks", "drift", "flow", "feed"] as const;
type View = (typeof VIEWS)[number];
const NEEDS = "a scrum master, product owner, manager, executive or admin";

const LINKABLE = new Set(["program", "project", "workstream", "pod"]);

/**
 * Where the story disagrees with the facts: risks from hard signals, drift
 * between what was reported and what happened, watermelons (a green parent
 * over a red child), flow, and the last week's activity, in one stream.
 */
export function SignalsPage() {
  const { canReadAggregate } = useRole();
  const [search, setSearch] = useSearchParams();
  const view: View = VIEWS.includes(search.get("view") as View)
    ? (search.get("view") as View)
    : "everything";

  const risks = useQuery({
    queryKey: ["portfolio", "risks"],
    queryFn: () => apiClient.portfolioRisks(),
    enabled: canReadAggregate,
  });
  const flow = useQuery({
    queryKey: ["portfolio", "flow"],
    queryFn: () => apiClient.portfolioFlow(),
    enabled: canReadAggregate,
  });
  const feed = useQuery({
    queryKey: ["portfolio", "feed"],
    queryFn: () => apiClient.portfolioFeed(null, 50),
    enabled: canReadAggregate && (view === "everything" || view === "feed"),
  });

  const r = risks.data?.risks ?? [];
  const d = risks.data?.drift ?? [];
  const watermelons = r.filter((x) => x.is_watermelon).length;

  return (
    <>
      <SectionHeader
        title="Signals"
        meta={
          risks.data
            ? `${r.length} open risks · ${watermelons} watermelons · ${d.length} drift findings`
            : "Risks, drift, flow and activity from Jira and Git, independent of what anyone reports."
        }
      />
      <PanelState locked={!canReadAggregate} needs={NEEDS} isLoading={false} error={null}>
        <ChipPicker
          label="Show"
          value={view}
          onChange={(next) =>
            setSearch(next === "everything" ? {} : { view: next }, { replace: true })
          }
          options={[
            { value: "everything", label: "Everything" },
            { value: "risks", label: `Risks${risks.data ? ` · ${r.length}` : ""}` },
            { value: "drift", label: `Drift${risks.data ? ` · ${d.length}` : ""}` },
            { value: "flow", label: "Flow" },
            { value: "feed", label: "Feed" },
          ]}
        />
        <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
          {view === "everything" || view === "flow" ? (
            <PanelState needs={NEEDS} isLoading={flow.isLoading} error={flow.error}>
              {flow.data ? <FlowBlock flow={flow.data} full={view === "flow"} /> : null}
            </PanelState>
          ) : null}
          {view === "everything" || view === "risks" || view === "drift" ? (
            <PanelState
              needs={NEEDS}
              isLoading={risks.isLoading}
              error={risks.error}
              isEmpty={
                (view === "risks"
                  ? r.length
                  : view === "drift"
                    ? d.length
                    : r.length + d.length) === 0
              }
              emptyText="No open findings."
            >
              <Stream risks={view === "drift" ? [] : r} drift={view === "risks" ? [] : d} />
            </PanelState>
          ) : null}
          {view === "everything" || view === "feed" ? (
            <Panel title="Activity" note="last 7 days · current, whatever the day">
              <PanelState
                needs={NEEDS}
                isLoading={feed.isLoading}
                error={feed.error}
                isEmpty={(feed.data?.items ?? []).length === 0}
                emptyText="Nothing happened in the last 7 days."
              >
                <ul>
                  {(feed.data?.items ?? []).slice(0, view === "feed" ? 50 : 10).map((item, i) => (
                    <li
                      key={`${item.observed_at}-${i}`}
                      className="flex min-w-0 items-start gap-3 border-t border-grey-border py-2.5 first:border-t-0"
                    >
                      <span className="w-[92px] flex-none text-[12px] text-grey-secondary tabular-nums">
                        {formatDay(item.observed_at)} {formatTime(item.observed_at)}
                      </span>
                      <span className="min-w-0 flex-1 text-[14px]">
                        {item.summary}
                        <span className="block text-[12px] text-grey-secondary">
                          {item.kind.replace(/_/g, " ")} · {item.source}
                          {item.person_name ? ` · ${item.person_name}` : ""}
                        </span>
                      </span>
                    </li>
                  ))}
                </ul>
              </PanelState>
            </Panel>
          ) : null}
        </div>
      </PanelState>
    </>
  );
}

function Stream({ risks, drift }: { risks: RiskFindingResponse[]; drift: DriftFindingResponse[] }) {
  const names = useNames();
  const items = [
    ...risks.map((x) => ({ type: "risk" as const, sev: x.severity, age: x.age_days, x })),
    ...drift.map((x) => ({ type: "drift" as const, sev: x.severity, age: 0, x })),
  ].sort((a, b) => ragSeverity(b.sev) - ragSeverity(a.sev) || b.age - a.age);

  return (
    <ul className="grid grid-cols-[minmax(0,1fr)] gap-3">
      {items.map((item, i) => {
        const ref = item.x.entity_ref;
        const link = LINKABLE.has(ref.kind) ? `/delivery/${ref.kind}/${ref.id}` : null;
        return (
          <li
            key={`${item.type}-${ref.id}-${i}`}
            className="rounded-3xl border border-grey-border p-4"
          >
            <div className="flex flex-wrap items-center gap-2">
              <RagDot rag={item.sev} />
              <span className="text-[15px] font-extrabold">{item.x.reason}</span>
              <RagBadge rag={item.sev} />
              {item.type === "risk" && item.x.is_watermelon ? (
                <RagChip tone="danger" className="h-6 px-2.5 text-[12px]">
                  watermelon
                </RagChip>
              ) : null}
              <span className="ml-auto text-[12px] font-bold text-grey-secondary">
                {item.type === "risk"
                  ? `${item.x.age_days}d`
                  : `since ${formatDay(item.x.detected_at)}`}
              </span>
            </div>
            <p className="mt-1 text-[12px] text-grey-secondary">
              {item.type === "risk"
                ? item.x.rule_id.replace(/_/g, " ")
                : `drift · ${item.x.kind.replace(/_/g, " ")}`}{" "}
              ·{" "}
              {link ? (
                <Link to={link}>
                  {ref.kind} {ref.id}
                </Link>
              ) : (
                `${ref.kind} ${ref.id}`
              )}
              {item.x.evidence && item.x.evidence.identifier !== ref.id
                ? ` · ${item.x.evidence.identifier}`
                : ""}
            </p>
            {item.type === "risk" ? (
              <div className="mt-3 grid grid-cols-[minmax(0,1fr)] gap-2 sm:grid-cols-2">
                <div className="rounded-2xl bg-grey-fill p-3 text-[13px]">
                  <p className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
                    Owner says · {item.x.person_name ?? names(item.x.owner_id)}
                  </p>
                  <p className="mt-1">
                    {item.x.owner_status_summary ?? "Nothing reported."}
                    {item.x.owner_status_source ? (
                      <span className="text-grey-secondary">
                        {" "}
                        ({sourceLine(item.x.owner_status_source)})
                      </span>
                    ) : null}
                  </p>
                </div>
                <div className="rounded-2xl bg-rag-red-bg/50 p-3 text-[13px]">
                  <p className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
                    Signals say
                  </p>
                  <p className="mt-1">{item.x.reason}</p>
                </div>
              </div>
            ) : (
              <p className="mt-2 text-[13px] text-grey-body">
                Owner: {names(item.x.owner_id)}
                {item.x.stated_source ? ` · stated ${sourceLine(item.x.stated_source)}` : ""}
                {item.x.child_entity_ref
                  ? ` · hides ${item.x.child_entity_ref.kind} ${item.x.child_entity_ref.id}`
                  : ""}
              </p>
            )}
          </li>
        );
      })}
    </ul>
  );
}

function FlowBlock({ flow, full }: { flow: PortfolioFlowResponse; full: boolean }) {
  const kpi = (label: string, value: string, note?: string) => (
    <div className="rounded-2xl border border-grey-border p-3">
      <p className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">{label}</p>
      <p className="mt-1 text-[22px] font-extrabold tabular-nums">{value}</p>
      {note ? <p className="text-[11px] text-grey-secondary">{note}</p> : null}
    </div>
  );
  const days = (v: number | null) => (v === null ? "—" : `${v.toFixed(1)}d`);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-6">
        {kpi("Active", String(flow.active_count))}
        {kpi("Features in flight", String(flow.features_in_flight))}
        {kpi("Stale", String(flow.stale_count), "7 days or more")}
        {kpi("Abandoned", String(flow.abandoned_count), "21 days or more")}
        {kpi("Cycle time", days(flow.avg_cycle_time_days), "average")}
        {kpi("PR age", days(flow.avg_pr_age_days), "average")}
      </div>
      {full && flow.workstreams.length > 0 ? (
        <TableBox>
          <table className="w-full min-w-[560px] border-collapse">
            <thead>
              <tr>
                <th className={th}>Workstream</th>
                <th className={`${th} text-right`}>Active</th>
                <th className={`${th} text-right`}>Stale</th>
                <th className={`${th} text-right`}>Abandoned</th>
                <th className={`${th} text-right`}>Cycle time</th>
              </tr>
            </thead>
            <tbody>
              {flow.workstreams.map((w) => (
                <tr key={w.workstream_id}>
                  <td className={td}>
                    <Link to={`/delivery/workstream/${w.workstream_id}`}>{w.workstream_name}</Link>
                  </td>
                  <td className={`${td} text-right tabular-nums`}>{w.active_count}</td>
                  <td className={`${td} text-right tabular-nums`}>{w.stale_count}</td>
                  <td className={`${td} text-right tabular-nums`}>{w.abandoned_count}</td>
                  <td className={`${td} text-right tabular-nums`}>{days(w.avg_cycle_time_days)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </TableBox>
      ) : null}
    </div>
  );
}
