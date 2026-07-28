import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { Sparkline } from "../../components/ui/Sparkline";
import { cn } from "../../lib/utils";
import { firstItemId } from "../../lib/selection";
import { toneForRag, toneHex } from "../../lib/status";
import { todayIso } from "../../lib/today";
import type { DirectoryItemResponse, Rag } from "../../api/schema";

const HERO_BG: Record<string, string> = {
  danger: "bg-rag-red-bg",
  warning: "bg-rag-amber-bg",
  success: "bg-rag-green-bg",
  neutral: "bg-rag-unknown-bg",
  info: "bg-rag-info-bg",
};

const HERO_TEXT: Record<string, string> = {
  danger: "text-rag-red",
  warning: "text-rag-amber-deep",
  success: "text-rag-green",
  neutral: "text-rag-unknown",
  info: "text-rag-info",
};

export function ManagerExecToday({ role }: { role: "mgr" | "exec" }) {
  const asOf = todayIso();
  const navigate = useNavigate();

  const programs = useQuery({
    queryKey: ["directory", "programs", asOf],
    queryFn: () => apiClient.programs(asOf),
  });
  const programId = firstItemId(programs.data ?? []);
  const program = programs.data?.find((item) => item.id === programId);

  const projects = useQuery({
    queryKey: ["directory", "projects", asOf],
    queryFn: () => apiClient.projects(asOf),
  });
  const workstreams = useQuery({
    queryKey: ["directory", "workstreams", asOf],
    queryFn: () => apiClient.workstreams(asOf),
  });
  const pods = useQuery({
    queryKey: ["directory", "pods", asOf],
    queryFn: () => apiClient.pods(asOf),
  });

  const trend = useQuery({
    queryKey: ["persona", "trend", programId, asOf],
    queryFn: () => apiClient.nodeTrend("program", programId, { asOf, windowDays: 30 }),
    enabled: Boolean(programId) && role === "mgr",
    staleTime: 5 * 60_000,
  });

  const risks = useQuery({
    queryKey: ["persona", "portfolio-risks", asOf],
    queryFn: () => apiClient.portfolioRisks(asOf),
  });

  const briefs = useQuery({
    queryKey: ["persona", "briefs"],
    queryFn: () => apiClient.personaBriefs(undefined, 20),
    staleTime: 5 * 60_000,
  });

  const heatRows: { label: string; kind: "project" | "workstream" | "pod"; items: DirectoryItemResponse[] }[] =
    [
      { label: "Projects", kind: "project", items: (projects.data ?? []).slice(0, 4) },
      { label: "Workstreams", kind: "workstream", items: (workstreams.data ?? []).slice(0, 4) },
      { label: "Pods", kind: "pod", items: (pods.data ?? []).slice(0, 4) },
    ];

  const worstRag = worstOf(
    [...(projects.data ?? []), ...(workstreams.data ?? []), ...(pods.data ?? [])].map((i) => i.rag),
  );
  const heroTone = toneForRag(worstRag);
  const heroLabel =
    worstRag === "red"
      ? "is at risk"
      : worstRag === "amber"
        ? "needs attention"
        : "is on track";

  const momentum = momentumLabel(trend.data?.points);
  const topRisks = [...(risks.data?.risks ?? [])]
    .sort((a, b) => b.age_days - a.age_days)
    .slice(0, 3);
  const topBriefs = (briefs.data?.briefs ?? []).slice(0, 3);

  return (
    <div className="flex flex-col gap-6">
      <Card
        padding="p-7"
        className={cn(HERO_BG[heroTone], "border-none")}
        animateDelay={70}
      >
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[1fr_380px]">
          <div>
            <div className="flex items-center gap-3">
              <span
                className={cn(
                  "h-3 w-3 rounded-full",
                  heroTone === "danger" ? "animate-op-pulse" : "",
                )}
                style={{ backgroundColor: toneHex[heroTone] }}
              />
              <h2 className={cn("text-[26px] font-extrabold", HERO_TEXT[heroTone])}>
                {program?.name ?? "Program"} {heroLabel}
              </h2>
            </div>
            <p className={cn("mt-3 max-w-[560px] text-[16px]", HERO_TEXT[heroTone])}>
              {topRisks[0]?.reason ??
                "No material risks detected across projects, workstreams, or pods right now."}
            </p>
          </div>
          {role === "mgr" ? (
            <div>
              <div className="text-xs font-bold uppercase tracking-wide text-grey-secondary">
                30-day momentum · {momentum}
              </div>
              <div className="mt-2">
                <Sparkline points={trend.data?.points ?? []} width={320} height={72} />
              </div>
            </div>
          ) : null}
        </div>
      </Card>

      <Card padding="p-6" animateDelay={140}>
        <h2 className="text-[18px] font-bold">Portfolio heat</h2>
        <div className="mt-4 flex flex-col gap-2.5">
          {heatRows.map((row) => (
            <div key={row.kind} className="grid grid-cols-[110px_repeat(4,1fr)] items-center gap-2.5">
              <div className="text-[13px] font-bold text-grey-secondary">{row.label}</div>
              {row.items.map((item, index) => {
                const tone = toneForRag(item.rag);
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => navigate(`/delivery/${row.kind}/${encodeURIComponent(item.id)}`)}
                    style={{ animationDelay: `${index * 50}ms` }}
                    className={cn(
                      "animate-op-pop h-[76px] rounded-2xl px-3 py-2 text-left transition-transform hover:-translate-y-0.5 hover:shadow-op-hover",
                      HERO_BG[tone],
                    )}
                  >
                    <div className={cn("truncate text-[14px] font-extrabold", HERO_TEXT[tone])}>
                      {item.name}
                    </div>
                    <div className={cn("mt-1 text-xs font-bold uppercase", HERO_TEXT[tone])}>
                      {item.rag ?? "unknown"}
                    </div>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </Card>

      <Card padding="p-0" animateDelay={210}>
        <div className="flex items-center justify-between px-6 pt-6 pb-2">
          <h2 className="text-[18px] font-bold">Top signals</h2>
          <button
            type="button"
            onClick={() => navigate("/signals")}
            className="text-[14px] font-bold text-magenta"
          >
            All signals
          </button>
        </div>
        {topRisks.map((risk) => (
          <div
            key={`${risk.rule_id}-${risk.entity_ref.id}`}
            className="flex items-center gap-3.5 border-t border-grey-fill px-6 py-4 first:border-t-0"
          >
            <span
              className={cn(
                "h-2.5 w-2.5 shrink-0 rounded-full",
                risk.severity === "red" ? "animate-op-pulse" : "",
              )}
              style={{ backgroundColor: toneHex[toneForRag(risk.severity)] }}
            />
            <div className="min-w-0 flex-1">
              <div className="truncate text-[15px] font-bold">{risk.reason}</div>
              <div className="mt-0.5 text-[13px] text-grey-secondary">
                {risk.entity_ref.kind} · {risk.entity_ref.id}
              </div>
            </div>
            <div className="shrink-0 text-[13px] font-bold text-grey-secondary">
              {risk.age_days}d
            </div>
          </div>
        ))}
      </Card>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-3">
        {topBriefs.map((brief) => (
          <Card key={`${brief.kind}-${brief.scope_id}-${brief.generated_at}`} padding="p-6" animateDelay={280}>
            <div className="text-xs font-bold uppercase tracking-wide text-magenta">
              {briefKindLabel(brief.kind)}
            </div>
            <h3 className="mt-2 text-[17px] font-bold">{brief.title}</h3>
            <div className="mt-1 text-xs text-grey-secondary">
              {new Date(brief.generated_at).toLocaleDateString()}
            </div>
            <p className="mt-2 text-[14px] leading-relaxed text-grey-body">{brief.body}</p>
          </Card>
        ))}
      </div>
    </div>
  );
}

function worstOf(rags: (Rag | null | undefined)[]): Rag {
  if (rags.some((rag) => rag === "red")) return "red";
  if (rags.some((rag) => rag === "amber")) return "amber";
  if (rags.some((rag) => rag === "green")) return "green";
  return "unknown";
}

function momentumLabel(points: { score: number }[] | undefined): string {
  if (!points || points.length < 2) return "steady";
  const first = points[0].score;
  const last = points[points.length - 1].score;
  if (last > first + 0.05) return "improving";
  if (last < first - 0.05) return "sliding";
  return "steady";
}

function briefKindLabel(kind: string): string {
  if (kind === "daily_pod") return "Daily pod";
  if (kind === "weekly_project") return "Weekly project";
  return "Exec";
}
