import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";

import type { PortfolioProjectDto } from "../../api/schema";
import { useProjects, useWorkstreams } from "../../app/directory";
import { useRole } from "../../app/role";
import { useViewingDate } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { TabIntro } from "../../features/hub/TabIntro";
import { Pill } from "../../components/ui/Pill";
import { formatTime } from "../../lib/format";
import { BarLegend } from "../../features/portfolio/ProjectBar";
import { ProjectCard } from "../../features/portfolio/ProjectCard";
import { PortfolioTiles } from "../../features/portfolio/PortfolioTiles";
import { ProgrammeLine } from "../../features/portfolio/ProgrammeLine";
import { ProgrammePicker } from "../../features/portfolio/ProgrammePicker";
import { usePortfolio } from "../../features/portfolio/usePortfolio";
import { useProgrammes } from "../../features/portfolio/useProgrammes";
import { WindowedList } from "../../features/portfolio/WindowedList";
import {
  asOfWords,
  byProgramme,
  filtersOf,
  programmeLine,
  toggledVerdict,
} from "../../features/portfolio/portfolioWords";

type Row =
  | { kind: "programme"; id: string; name: string; line: string | null; count: number }
  | { kind: "project"; project: PortfolioProjectDto };

/**
 * All projects › Will we make the date? (`/portfolio/date`), the landing of
 * every reader of project lists: every project the person reads, worst
 * first, from the kept forecast rows in one read a page, never a live
 * forecast per project. The count tiles are the filters; search and the
 * programme stay in the URL (`?q=`, `?verdict=`, `?program=`). Programmes group
 * the list for the readers of programmes (manager, executive, admin). Past a
 * hundred projects only the cards near the screen are drawn.
 */
export function PortfolioDate() {
  const { canReadPortfolio } = useRole();
  const { chosen, today } = useViewingDate();
  const [search, setSearch] = useSearchParams();
  const filters = filtersOf(search);
  const portfolio = usePortfolio(filters);
  const { programmes } = useProgrammes();
  const directory = useProjects();
  const workstreams = useWorkstreams();
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
  // The workstreams each project uses, by name, for the card's one muted line:
  // they are optional, so a project that uses none says nothing about them.
  const workstreamsOf = (projectId: string): string[] => {
    const ids = directory.data?.find((item) => item.id === projectId)?.workstream_ids ?? [];
    return (workstreams.data ?? [])
      .filter((item) => ids.includes(item.id) && item.in_use !== false)
      .map((item) => item.name);
  };
  const asOf = first ? asOfWords(first.computed_at) : null;
  const meta = [
    "Across the portfolio: three dates side by side, one colour and the reason, worst first.",
    asOf && first?.computed_at ? `${asOf}, ${formatTime(first.computed_at)}` : asOf,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div>
      <TabIntro tab="date" meta={meta} />

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
        <ProgrammePicker
          id="portfolio-program"
          every
          programmes={programmes}
          value={filters.program ?? ""}
          onChange={(id) => {
            const next = new URLSearchParams(search);
            if (id) next.set("program", id);
            else next.delete("program");
            update(next);
          }}
        />
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
                    <ProgrammeLine id={row.id} name={row.name} line={row.line} />
                  ) : (
                    <div className="pb-4">
                      <ProjectCard
                        project={row.project}
                        today={today}
                        shownDay={shownDay}
                        viewingPast={chosen !== null}
                        workstreams={workstreamsOf(row.project.id)}
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
