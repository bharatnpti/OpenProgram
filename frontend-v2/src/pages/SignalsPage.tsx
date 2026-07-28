import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { apiClient } from "../api/client";
import { FlowStrip } from "../features/signals/FlowStrip";
import { SignalCard, type SignalCardData } from "../features/signals/SignalCard";
import { toneForRag } from "../lib/status";
import { todayIso } from "../lib/today";
import { cn } from "../lib/utils";
import type { DriftFindingResponse, RiskFindingResponse } from "../api/schema";

type FilterKey = "all" | "risk" | "drift" | "flow" | "feed";

const FILTERS: { key: FilterKey; label: string }[] = [
  { key: "all", label: "Everything" },
  { key: "risk", label: "Risks" },
  { key: "drift", label: "Drift" },
  { key: "flow", label: "Flow" },
  { key: "feed", label: "Feed" },
];

function riskCard(risk: RiskFindingResponse, index: number): SignalCardData {
  return {
    id: `risk-${risk.rule_id}-${risk.entity_ref.id}-${index}`,
    tone: toneForRag(risk.severity),
    title: risk.reason,
    category: "risk",
    entityLabel: `${risk.entity_ref.kind} · ${risk.entity_ref.id}`,
    ageLabel: `${risk.age_days}d`,
    watermelon: risk.is_watermelon,
    ownerSays: risk.owner_status_summary ?? "No check-in on record",
    signalsSay: `${risk.reason} (${risk.evidence.identifier})`,
    animateDelay: Math.min(index * 70, 420),
  };
}

function driftCard(drift: DriftFindingResponse, index: number): SignalCardData {
  return {
    id: `drift-${drift.kind}-${drift.entity_ref.id}-${index}`,
    tone: toneForRag(drift.severity),
    title: drift.reason,
    category: "drift",
    entityLabel: `${drift.entity_ref.kind} · ${drift.entity_ref.id}`,
    ageLabel: new Date(drift.detected_at).toLocaleDateString(),
    ownerSays: drift.stated_source ? `Stated via ${drift.stated_source}` : "No stated status",
    signalsSay: drift.reason,
    animateDelay: Math.min(index * 70, 420),
  };
}

export function SignalsPage() {
  const asOf = todayIso();
  const [filter, setFilter] = useState<FilterKey>("all");

  const risks = useQuery({
    queryKey: ["persona", "portfolio-risks", asOf],
    queryFn: () => apiClient.portfolioRisks(asOf),
  });
  const flow = useQuery({
    queryKey: ["persona", "portfolio-flow", asOf],
    queryFn: () => apiClient.portfolioFlow(asOf),
  });
  const feed = useQuery({
    queryKey: ["persona", "portfolio-feed"],
    queryFn: () => apiClient.portfolioFeed(),
    staleTime: 60_000,
  });

  const riskCards = (risks.data?.risks ?? []).map(riskCard);
  const driftCards = (risks.data?.drift ?? []).map(driftCard);
  const feedCards: SignalCardData[] = (feed.data?.items ?? []).map((item, index) => ({
    id: `feed-${item.entity_ref.id}-${item.observed_at}-${index}`,
    tone: "info",
    title: item.summary,
    category: "feed",
    entityLabel: `${item.entity_ref.kind} · ${item.entity_ref.id}`,
    ageLabel: new Date(item.observed_at).toLocaleDateString(),
    signalsSay: `${item.source} · ${item.kind}`,
    animateDelay: Math.min(index * 70, 420),
  }));

  const watermelons = (risks.data?.risks ?? []).filter((risk) => risk.is_watermelon).length;

  const cards =
    filter === "risk"
      ? riskCards
      : filter === "drift"
        ? driftCards
        : filter === "feed"
          ? feedCards
          : filter === "flow"
            ? []
            : [...riskCards, ...driftCards, ...feedCards];

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-6">
        <div>
          <h1 className="text-[28px] font-extrabold">Signals</h1>
          <p className="mt-1 max-w-[520px] text-[15px] text-grey-secondary">
            Risks, drift, and portfolio activity in one stream — where owner narrative and signals
            disagree.
          </p>
        </div>
        <div className="flex flex-wrap gap-6">
          <Stat label="Open risks" value={risks.data?.risks?.length ?? 0} />
          <Stat label="Watermelons" value={watermelons} tone="text-rag-red" />
          <Stat label="Drift findings" value={risks.data?.drift?.length ?? 0} />
          <Stat label="Stale items" value={flow.data?.stale_count ?? 0} tone="text-rag-amber" />
        </div>
      </div>

      <div className="flex flex-wrap gap-2">
        {FILTERS.map((item) => (
          <button
            key={item.key}
            type="button"
            onClick={() => setFilter(item.key)}
            className={cn(
              "flex h-10 items-center rounded-full px-4 text-[14px] font-bold",
              filter === item.key
                ? "bg-magenta text-white"
                : "border border-grey-border bg-white text-ink hover:bg-grey-fill",
            )}
          >
            {item.label}
          </button>
        ))}
      </div>

      {filter === "flow" ? (
        <div className="flex max-w-[920px] flex-col gap-3">
          {(flow.data?.workstreams ?? []).map((ws) => (
            <div
              key={ws.workstream_id}
              className="flex items-center justify-between gap-4 rounded-2xl border border-grey-border bg-white px-5 py-4"
            >
              <div className="font-bold">{ws.workstream_name}</div>
              <div className="flex gap-6 text-[13px] text-grey-secondary">
                <span>{ws.active_count} active</span>
                <span className={ws.stale_count > 0 ? "text-rag-amber" : undefined}>
                  {ws.stale_count} stale
                </span>
                <span className={ws.abandoned_count > 0 ? "text-rag-red" : undefined}>
                  {ws.abandoned_count} abandoned
                </span>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="flex max-w-[920px] flex-col gap-3.5">
          {cards.map((card) => (
            <SignalCard key={card.id} data={card} />
          ))}
          {cards.length === 0 ? (
            <p className="text-sm text-grey-secondary">Nothing to show for this filter.</p>
          ) : null}
        </div>
      )}

      <FlowStrip flow={flow.data} />
    </div>
  );
}

function Stat({ label, value, tone }: { label: string; value: number; tone?: string }) {
  return (
    <div>
      <div className={cn("tabular-nums text-[32px] font-extrabold", tone)}>{value}</div>
      <div className="mt-0.5 text-[13px] text-grey-secondary">{label}</div>
    </div>
  );
}
