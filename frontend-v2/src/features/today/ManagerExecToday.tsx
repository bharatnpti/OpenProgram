import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { apiClient } from "../../api/client";
import { Card } from "../../components/ui/Card";
import { Sparkline } from "../../components/ui/Sparkline";
import { cn } from "../../lib/utils";
import { firstItemId } from "../../lib/selection";
import { briefsHref, localDay } from "../../lib/briefs";
import { ragSeverity, toneForRag, toneHex } from "../../lib/status";
import { useViewingDate } from "../../app/viewingDate";
import type { BriefKind, DirectoryItemResponse, Rag } from "../../api/schema";

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

/**
 * Today for manager, exec and admin. All three see the same portfolio-wide
 * screen: every read on it is an aggregate each of them is granted, so nothing
 * here is held back by role.
 */
export function ManagerExecToday() {
  const { asOf } = useViewingDate();
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

  // The exec gets this as well as the manager: the program trend is the same
  // aggregate the exec already reads as heat, and the direction of travel is
  // the at-a-glance answer a portfolio owner opens this screen for.
  const trend = useQuery({
    queryKey: ["persona", "trend", programId, asOf],
    queryFn: () => apiClient.nodeTrend("program", programId, { asOf, windowDays: 30 }),
    enabled: Boolean(programId),
    staleTime: 5 * 60_000,
  });

  const risks = useQuery({
    queryKey: ["persona", "portfolio-risks", asOf],
    queryFn: () => apiClient.portfolioRisks(asOf),
  });

  // Each row is a fixed four columns, so a tenant with more than four of a
  // kind cannot show them all. Order worst-first before truncating: taking the
  // first four in directory order (which is alphabetical) hid the tenant's
  // only red workstream -- "Payments API" -- behind three ambers, so the row
  // read as "nothing red here" while the hero, computed over *every* item,
  // said the program was at risk. The count names what is left out.
  const heatRows = [
    heatRow("Projects", "project", projects.data),
    heatRow("Workstreams", "workstream", workstreams.data),
    heatRow("Pods", "pod", pods.data),
  ];

  const worstRag = worstOf(
    [...(projects.data ?? []), ...(workstreams.data ?? []), ...(pods.data ?? [])].map((i) => i.rag),
  );
  const heroTone = toneForRag(worstRag);
  // `worstOf` returns "unknown" when nothing is red, amber or green -- which is
  // also what it returns while the directory is loading, empty, or failed. Only
  // an actual green reading may be reported as on track; silence is never green.
  const heatLoading = projects.isLoading || workstreams.isLoading || pods.isLoading;
  const heatFailed = projects.isError || workstreams.isError || pods.isError;
  const heroLabel = heatFailed
    ? "status could not be loaded"
    : heatLoading
      ? "status is loading"
      : worstRag === "red"
        ? "is at risk"
        : worstRag === "amber"
          ? "needs attention"
          : worstRag === "green"
            ? "is on track"
            : "has no confirmed status";

  // A momentum reading is a claim about the data, so loading and failure get
  // said out loud rather than folded into a direction. The trend waits on the
  // program list for its id, so that list's state counts too: without it the
  // label read "not available" until the programs arrived.
  const momentum =
    programs.isError || trend.isError
      ? "could not be loaded"
      : programs.isLoading || trend.isLoading
        ? "loading…"
        : momentumLabel(trend.data?.points);
  // The line is drawn over the same days the label is measured over. Plotted
  // at its score of 0, an `unknown` day sits below red, so a program that
  // opened unknown and then went red drew that step as a climb.
  const momentumPoints = reportedDays(trend.data?.points ?? []);
  const topRisks = [...(risks.data?.risks ?? [])]
    .sort((a, b) => b.age_days - a.age_days)
    .slice(0, 3);
  // "No material risks detected" is only true once the risk query has come back
  // with none of them.
  const heroDetail = risks.isError
    ? "Risks could not be loaded, so this is not an all-clear."
    : (topRisks[0]?.reason ??
      (risks.isLoading
        ? "Checking for open risks\u2026"
        : heatFailed || heatLoading || worstRag === "unknown"
          ? "Nothing has reported a status yet."
          : "No material risks detected across projects, workstreams, or pods right now."));

  return (
    <div className="flex flex-col gap-6">
      <Card padding="p-7" className={cn(HERO_BG[heroTone], "border-none")} animateDelay={70}>
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
              {heroDetail}
            </p>
          </div>
          <div>
            <div className="text-xs font-bold uppercase tracking-wide text-grey-secondary">
              30-day momentum · {momentum}
            </div>
            <div className="mt-2">
              <Sparkline points={momentumPoints} width={320} height={72} />
            </div>
          </div>
        </div>
      </Card>

      <LeadBrief animateDelay={140} />

      <Card padding="p-6" animateDelay={210}>
        <h2 className="text-[18px] font-bold">Portfolio heat</h2>
        <div className="mt-4 flex flex-col gap-2.5">
          {heatRows.map((row) => (
            <div
              key={row.kind}
              className="grid grid-cols-[110px_repeat(4,1fr)] items-center gap-2.5"
            >
              <div className="text-[13px] font-bold text-grey-secondary">
                {row.label}
                {row.total > row.items.length ? (
                  <span className="block text-[11px] font-bold uppercase tracking-wide">
                    worst {row.items.length} of {row.total}
                  </span>
                ) : null}
              </div>
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

      <Card padding="p-0" animateDelay={280}>
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
            {/* "open Nd" -- see SignalsPage: this is the age now, while the
                reason beside it states the age at detection. */}
            <div className="shrink-0 text-[13px] font-bold text-grey-secondary">
              open {risk.age_days}d
            </div>
          </div>
        ))}
      </Card>
    </div>
  );
}

/**
 * The kind of brief this screen leads with, for every role that lands here.
 *
 * Manager, exec and admin all see this screen, and each may read every brief
 * kind (the endpoint asks only for an aggregate read), so permission does not
 * pick the brief -- scope does. Everything else here is portfolio-wide, and the
 * exec brief is the one brief written at that scope, from the same heatmap and
 * feed. Leading with a weekly project brief would put one project above the
 * rest for no reason; those are a click away on Coordination.
 */
const LEAD_BRIEF_KIND: BriefKind = "exec";

/** The newest exec brief, or a plain statement that there is none. */
function LeadBrief({ animateDelay }: { animateDelay?: number }) {
  const navigate = useNavigate();

  // Asked of the endpoint by kind, not picked out of the newest twenty of any
  // kind: daily pod briefs fill those before last week's exec brief appears.
  // With no exec brief the card says so rather than standing another kind in.
  //
  // The endpoint has no as-of, so on a past day the card takes the newest
  // brief generated on or before it: a brief written after the day being
  // viewed would describe a portfolio the reader is not looking at.
  const { asOf, isPast, label } = useViewingDate();
  const lead = useQuery({
    queryKey: ["persona", "briefs", LEAD_BRIEF_KIND, "recent"],
    queryFn: () => apiClient.personaBriefs(LEAD_BRIEF_KIND, 10),
    staleTime: 5 * 60_000,
  });
  const brief = lead.data?.briefs.find((item) => localDay(item.generated_at) <= asOf);

  return (
    <Card padding="p-6" animateDelay={animateDelay}>
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h2 className="text-[18px] font-bold">{brief?.title ?? "Executive brief"}</h2>
          {brief ? (
            <div className="mt-1 text-xs text-grey-secondary">
              {new Date(brief.generated_at).toLocaleDateString()}
            </div>
          ) : null}
        </div>
        <button
          type="button"
          onClick={() => navigate(briefsHref(LEAD_BRIEF_KIND))}
          className="shrink-0 text-[14px] font-bold text-magenta"
        >
          All briefs
        </button>
      </div>
      {brief ? (
        // Clamped so a long fallback body cannot push the heat map off the
        // screen; the full text is on Coordination.
        <p className="mt-3 line-clamp-6 max-w-[760px] text-[15px] leading-relaxed text-grey-body">
          {brief.body}
        </p>
      ) : (
        <p className="mt-3 text-sm text-grey-secondary">
          {lead.isError
            ? `The executive brief could not be loaded: ${
                lead.error instanceof Error ? lead.error.message : "unknown error"
              }`
            : lead.isLoading
              ? "Loading the executive brief…"
              : isPast
                ? `No executive brief was generated on or before ${label}.`
                : "No executive brief has been generated yet."}
        </p>
      )}
    </Card>
  );
}

function worstOf(rags: (Rag | null | undefined)[]): Rag {
  if (rags.some((rag) => rag === "red")) return "red";
  if (rags.some((rag) => rag === "amber")) return "amber";
  if (rags.some((rag) => rag === "green")) return "green";
  return "unknown";
}

const HEAT_COLUMNS = 4;

type HeatRow = {
  label: string;
  kind: "project" | "workstream" | "pod";
  items: DirectoryItemResponse[];
  total: number;
};

function heatRow(
  label: string,
  kind: "project" | "workstream" | "pod",
  data: DirectoryItemResponse[] | undefined,
): HeatRow {
  const all = data ?? [];
  const ranked = [...all].sort(
    (a, b) => ragSeverity(b.rag) - ragSeverity(a.rag) || a.name.localeCompare(b.name),
  );
  return { label, kind, items: ranked.slice(0, HEAT_COLUMNS), total: all.length };
}

type TrendPoint = { score: number; rag: Rag };

/** The days that reported a status -- the only ones on the health scale. */
function reportedDays<T extends TrendPoint>(points: T[]): T[] {
  return points.filter((point) => point.rag !== "unknown");
}

/**
 * Direction of travel across the window.
 *
 * A point whose status is `unknown` carries score 0 -- *below* `red` -- so it
 * is not a value on the health scale, and comparing it as one manufactures a
 * direction. The seeded program opens `unknown` and ends `red` after
 * seventeen straight red weekdays; first-vs-last on the raw score read that
 * as "improving", printed beside a hero saying "is at risk". So momentum is
 * measured only over days that actually reported a status, and says so when
 * there are not two of them rather than defaulting to "steady".
 */
function momentumLabel(points: TrendPoint[] | undefined): string {
  if (points === undefined) return "not available";
  const reported = reportedDays(points);
  if (reported.length < 2) return "not enough reported days";
  const first = reported[0].score;
  const last = reported[reported.length - 1].score;
  if (last > first + 0.05) return "improving";
  if (last < first - 0.05) return "sliding";
  return "steady";
}
