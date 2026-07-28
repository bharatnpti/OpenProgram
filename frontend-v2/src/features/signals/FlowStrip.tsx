import type { PortfolioFlowResponse } from "../../api/schema";

export function FlowStrip({ flow }: { flow: PortfolioFlowResponse | undefined }) {
  if (!flow) return null;

  const stats: [string, string | number, string?][] = [
    ["Active items", flow.active_count],
    ["Features in flight", flow.features_in_flight],
    ["Stale · 7d+", flow.stale_count, "text-rag-amber"],
    ["Abandoned · 21d+", flow.abandoned_count, "text-rag-red"],
    ["Avg cycle time", flow.avg_cycle_time_days != null ? `${flow.avg_cycle_time_days.toFixed(1)}d` : "—"],
    ["Avg PR age", flow.avg_pr_age_days != null ? `${flow.avg_pr_age_days.toFixed(1)}d` : "—"],
  ];

  return (
    <div className="flex flex-wrap gap-8 rounded-3xl bg-grey-fill p-6">
      {stats.map(([label, value, colorClass]) => (
        <div key={label}>
          <div className={`tabular-nums text-[24px] font-extrabold ${colorClass ?? ""}`}>
            {value}
          </div>
          <div className="mt-0.5 text-[13px] text-grey-secondary">{label}</div>
        </div>
      ))}
    </div>
  );
}
