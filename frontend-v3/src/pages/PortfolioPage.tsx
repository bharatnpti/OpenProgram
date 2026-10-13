import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import type { PortfolioProjectDto } from "../api/schema";
import { usePrograms } from "../app/directory";
import { useRole } from "../app/role";
import { useViewingDate } from "../app/viewingDate";
import { PanelState, SectionHeader } from "../components/PanelState";
import { Pill } from "../components/ui/Pill";
import { formatTime } from "../lib/format";
import { BarLegend } from "../features/portfolio/ProjectBar";
import { ProjectCard } from "../features/portfolio/ProjectCard";
import { PortfolioTiles } from "../features/portfolio/PortfolioTiles";
import { usePortfolio } from "../features/portfolio/usePortfolio";
import { WindowedList } from "../features/portfolio/WindowedList";
import {
  asOfWords,
  byProgramme,
  filtersOf,
  programmeLine,
  toggledVerdict,
} from "../features/portfolio/portfolioWords";

type Row =
  | { kind: "programme"; id: string; name: string; line: string | null; count: number }
  | { kind: "project"; project: PortfolioProjectDto };

/**
 * Will we make the date? (`/portfolio`): every project the person reads, worst
 * first, from the kept forecast rows in one read a page (D90), never a live
 * forecast per project. The count tiles are the filters; search and the
 * programme stay in the URL (`?q=`, `?verdict=`, `?program=`). Programmes group
 * the list for the readers of programmes (manager, executive, admin). Past a
 * hundred projects only the cards near the screen are drawn.
 */
export function PortfolioPage() {
  const { canReadPortfolio } = useRole();
  const { chosen, today } = useViewingDate();
  const [search, setSearch] = useSearchParams();
  const filters = filtersOf(search);
  const portfolio = usePortfolio(filters);
  const programs = usePrograms();
  const [typed, setTyped] = useState(filters.q);

  // The search box writes the URL a moment after typing stops.
  useEffect(() => {
    if (typed === filters.q) return;
    const timer = window.setTimeout(() => {
      setSearch(
        (current) => {
          const next = new URLSearchParams(current);
          if (typed.trim()) next.set("q", typed.trim());
          else next.delete("q");
          return next;
        },
        { replace: true },
      );
    }, 250);
    return () => window.clearTimeout(timer);
  }, [typed, filters.q, setSearch]);

  const pages = portfolio.data?.pages;
  const first = pages?.[0];
  const projects = useMemo(() => (pages ?? []).flatMap((page) => page.projects), [pages]);
  const shownDay = chosen ?? today;
  const rows = useMemo<Row[]>(() => {
    if (!canReadPortfolio || !first) {
      return projects.map((project) => ({ kind: "project", project }));
    }
    return byProgramme(projects, first.programs).flatMap((group): Row[] => [
      {
        kind: "programme",
        id: group.id,
        name: group.name,
        line: programmeLine(group.counts),
        count: group.projects.length,
      },
      ...group.projects.map((project): Row => ({ kind: "project", project })),
    ]);
  }, [canReadPortfolio, first, projects]);

  const update = (next: URLSearchParams) => setSearch(next, { replace: true });
  const programOptions = programs.data ?? [];
  const asOf = first ? asOfWords(first.computed_at) : null;
  const meta = [
    "Across the portfolio: three dates side by side, one colour and the reason, worst first.",
    asOf && first?.computed_at ? `${asOf}, ${formatTime(first.computed_at)}` : asOf,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className="op-viz">
      <SectionHeader title="Will we make the date?" meta={meta} />

      <div className="mb-5 flex flex-wrap items-end gap-3">
        <div className="min-w-0 flex-1 basis-[240px]">
          <label htmlFor="portfolio-search" className="mb-1 block text-[12px] font-bold">
            Search
          </label>
          <input
            id="portfolio-search"
            type="search"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            placeholder="Project, Jira key or programme"
            className="h-10 w-full rounded-full border border-grey-border bg-(--op-viz-surface) px-4 text-[14px] text-ink"
          />
        </div>
        {programOptions.length > 1 ? (
          <div className="min-w-0">
            <label htmlFor="portfolio-program" className="mb-1 block text-[12px] font-bold">
              Programme
            </label>
            <select
              id="portfolio-program"
              value={filters.program ?? ""}
              onChange={(event) => {
                const next = new URLSearchParams(search);
                if (event.target.value) next.set("program", event.target.value);
                else next.delete("program");
                update(next);
              }}
              className="h-10 max-w-full rounded-full border border-grey-border bg-(--op-viz-surface) px-4 text-[14px] font-bold text-ink"
            >
              <option value="">Every programme</option>
              {programOptions.map((program) => (
                <option key={program.id} value={program.id}>
                  {program.name}
                </option>
              ))}
            </select>
          </div>
        ) : null}
      </div>

      <PanelState
        isLoading={portfolio.isLoading}
        error={portfolio.error}
        onRetry={() => void portfolio.refetch()}
      >
        {first ? (
          <>
            <PortfolioTiles
              counts={first.counts}
              picked={filters.verdicts}
              onToggle={(filter) => update(toggledVerdict(search, filter))}
              onClear={() => {
                const next = new URLSearchParams(search);
                next.delete("verdict");
                update(next);
              }}
            />
            <p className="mb-3 text-[13px] text-grey-secondary" aria-live="polite">
              {first.total === 0
                ? filters.q || filters.verdicts.length || filters.program
                  ? "No project matches."
                  : "No projects are configured yet. An admin adds them under Admin → Entities."
                : `${first.total} ${first.total === 1 ? "project" : "projects"}, worst first.`}
              {portfolio.isPlaceholderData ? " Updating…" : ""}
            </p>
            <div className="grid grid-cols-[minmax(0,1fr)]">
              <WindowedList
                items={rows}
                keyOf={(row) =>
                  row.kind === "programme" ? `programme:${row.id}` : `project:${row.project.id}`
                }
                render={(row) =>
                  row.kind === "programme" ? (
                    <h3 className="mb-3 mt-4 flex flex-wrap items-baseline gap-x-3 gap-y-1 text-[15px] font-extrabold first:mt-0">
                      {row.name}
                      {row.line ? (
                        <span className="text-[12px] font-normal text-grey-secondary">
                          {row.line}
                        </span>
                      ) : null}
                    </h3>
                  ) : (
                    <div className="pb-4">
                      <ProjectCard
                        project={row.project}
                        today={today}
                        shownDay={shownDay}
                        viewingPast={chosen !== null}
                      />
                    </div>
                  )
                }
              />
            </div>
            {portfolio.hasNextPage ? (
              <div className="mt-2">
                <Pill
                  variant="ghost"
                  size="sm"
                  disabled={portfolio.isFetchingNextPage}
                  onClick={() => void portfolio.fetchNextPage()}
                >
                  {portfolio.isFetchingNextPage
                    ? "Loading…"
                    : `Show ${Math.min(50, first.total - projects.length)} more of ${first.total - projects.length}`}
                </Pill>
              </div>
            ) : null}
            {projects.length > 0 ? <BarLegend /> : null}
          </>
        ) : null}
      </PanelState>
    </div>
  );
}
