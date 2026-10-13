// Will we make the date? (`/portfolio`): the words and the geometry of a project's
// card, pure, from the bulk read (`GET /portfolio/delivery`, D90). The reason line
// and today's health are the server's words; this only arranges them. One colour
// per project, the date's verdict (D92). Type imports, and runtime imports by
// their .ts path, so `node --test` runs it as written.
import type {
  PortfolioMarkDto,
  PortfolioPodDto,
  PortfolioProgramDto,
  PortfolioProjectDto,
  PortfolioReleaseDto,
  Verdict,
  VerdictCountsDto,
  VerdictFilter,
} from "../../api/schema";
import { daysBetween, formatDay } from "../../lib/format.ts";
import { VERDICT_LABELS, toneForVerdict, type BadgeTone } from "../../lib/status.ts";

/** The filters the count tiles are, in the order they read: the date's four answers. */
export const TILE_FILTERS: readonly VerdictFilter[] = ["late", "at_risk", "no_date", "on_track"];

// The tiles say each verdict as every pill, Today and the Project page say it
// (lib/status VERDICT_LABELS): one word for one verdict. The filter key stays `late`.
const TILE_LABELS: Record<VerdictFilter, string> = {
  late: "Off track",
  at_risk: "At risk",
  no_date: "No committed date",
  on_track: "On track",
  done: "Done",
  not_enough_data: "Not enough history",
};

const TILE_VERDICT: Record<VerdictFilter, Verdict> = {
  late: "off_track",
  at_risk: "at_risk",
  no_date: "no_date",
  on_track: "on_track",
  done: "done",
  not_enough_data: "not_enough_data",
};

export type Tile = {
  filter: VerdictFilter;
  label: string;
  count: number;
  tone: BadgeTone;
};

/**
 * The count tiles over every project the search matches: On track, At risk,
 * Late and No committed date (red: our own rule, a date nobody committed to is
 * not on track). Each is a filter.
 */
export function verdictTiles(counts: VerdictCountsDto): Tile[] {
  return TILE_FILTERS.map((filter) => {
    const verdict = TILE_VERDICT[filter];
    return {
      filter,
      label: TILE_LABELS[filter],
      count: counts[verdict] ?? 0,
      tone: filter === "no_date" ? "danger" : toneForVerdict(verdict),
    };
  });
}

/** The verdict as the card's one pill. */
export function verdictPill(project: Pick<PortfolioProjectDto, "verdict" | "total">): {
  label: string;
  tone: BadgeTone;
} {
  if (!project.verdict) return { label: "No forecast yet", tone: "neutral" };
  // A date nobody committed to is not on track: red, as its tile is (our own rule).
  const tone = project.verdict === "no_date" ? "danger" : toneForVerdict(project.verdict);
  return { label: VERDICT_LABELS[project.verdict], tone };
}

/**
 * A pod row's date words: "Nothing in scope" for a pod with no requirements
 * counted (never "Done", as its date strip says it), else its verdict, else
 * that no forecast is kept yet.
 */
export function podDateWords(pod: Pick<PortfolioPodDto, "total" | "verdict">): string {
  if (pod.total <= 0) return "Nothing in scope";
  return pod.verdict ? VERDICT_LABELS[pod.verdict] : "No forecast yet";
}

/**
 * Today's health in words, after the reason (D92): "Today: 1 open blocker, 2 of 8
 * not replied". When the rollup it is read from was kept on an earlier day than
 * the one shown, that day is said instead of "today".
 */
export function healthLine(
  project: Pick<PortfolioProjectDto, "health" | "health_day">,
  shownDay: string,
  viewingPast: boolean,
): string {
  const day = project.health_day;
  const prefix =
    day && day < shownDay ? `On ${formatDay(day)}` : viewingPast ? "That day" : "Today";
  return `${prefix}: ${project.health}`;
}

/** "19 working days after the committed date", or how much room there is. */
export function lateWords(
  project: Pick<PortfolioProjectDto, "days_late" | "target_source">,
): string | null {
  const days = project.days_late;
  if (days === null || days === undefined) return null;
  const against =
    project.target_source === "jira_release" ? "Jira's release date" : "the committed date";
  const count = (n: number) => `${n} working ${n === 1 ? "day" : "days"}`;
  if (days > 0) return `85% date ${count(days)} after ${against}`;
  if (days < 0) return `85% date ${count(-days)} before ${against}`;
  return `85% date on ${against}`;
}

/** The three dates, each in words: committed, likely (the 50–85% range) and the team's own. */
export function threeDates(
  project: PortfolioProjectDto,
  today: string,
): {
  label: string;
  value: string;
  hint?: string;
}[] {
  const day = (iso: string) => dayIn(iso, today);
  const likely =
    project.p50 && project.p85
      ? project.p50 === project.p85
        ? day(project.p85)
        : `${day(project.p50)} – ${day(project.p85)}`
      : "Not forecast yet";
  return [
    {
      label: project.target_source === "jira_release" ? "Jira release" : "Committed",
      value: project.target ? day(project.target) : "None",
    },
    { label: "Likely (50–85%)", value: likely },
    {
      label: "Team's date",
      value: project.team_date ? day(project.team_date) : "None given",
      hint: project.team_key ?? undefined,
    },
  ];
}

/** "Tue 26 Jan", with the year when it is not today's: "Tue 26 Jan 2027". */
export function dayIn(iso: string, today: string): string {
  const words = formatDay(iso);
  return iso.slice(0, 4) === today.slice(0, 4) ? words : `${words} ${iso.slice(0, 4)}`;
}

/** The release chip: the first release by date, and how many more. */
export function releaseChip(releases: readonly PortfolioReleaseDto[]): string | null {
  if (releases.length === 0) return null;
  const more = releases.length - 1;
  return more > 0 ? `${releases[0].name} +${more}` : releases[0].name;
}

/** "Forecast as of Mon 12 Oct": the kept rows' day, never a live forecast. */
export function asOfWords(computedAt: string | null | undefined): string {
  return computedAt ? `Forecast as of ${formatDay(computedAt)}` : "No forecast kept yet";
}

const MARK_WORDS: Record<string, [string, string]> = {
  scope_added: ["requirement added", "requirements added"],
  scope_removed: ["requirement removed", "requirements removed"],
  reopened: ["requirement reopened", "requirements reopened"],
};

/** One change on the bar, in words: "12 Jun: 3 requirements added, +4.5 working days". */
export function markWords(mark: PortfolioMarkDto): string {
  const day = formatDay(mark.day);
  if (mark.kind === "committed_date_changed") {
    return mark.moved_to
      ? `${day}: committed date moved to ${formatDay(mark.moved_to)}`
      : `${day}: committed date cleared`;
  }
  const [one, many] = MARK_WORDS[mark.kind] ?? ["change", "changes"];
  const what = `${mark.count} ${mark.count === 1 ? one : many}`;
  if (mark.working_days === null || mark.working_days === undefined) {
    return `${day}: ${what}, days not known`;
  }
  const days = mark.working_days;
  const signed = days > 0 ? `+${days}` : days < 0 ? `−${Math.abs(days)}` : "±0";
  return `${day}: ${what}, ${signed} working ${Math.abs(days) === 1 ? "day" : "days"}`;
}

export type BarMarkAt = { at: number; pushed: boolean; label: string; key: string };

export type Bar = {
  start: string;
  end: string;
  /** Where each date sits on the bar, 0 to 1; null when the date is not known. */
  today: number;
  committed: number | null;
  likelyFrom: number | null;
  likelyTo: number | null;
  team: number | null;
  marks: BarMarkAt[];
  /** Done of total, 0 to 1; null with nothing counted. */
  done: number | null;
};

/**
 * The bar's scale: from planning (else a month back) to the latest of the dates
 * and today, a few days beyond, so every tick and mark has its place.
 */
export function barOf(project: PortfolioProjectDto, today: string): Bar {
  const dates = [project.target, project.p85, project.team_date, today].filter(
    (value): value is string => Boolean(value),
  );
  const start = project.start <= today ? project.start : today;
  const last = dates.reduce((latest, value) => (value > latest ? value : latest), today);
  const span = Math.max(daysBetween(start, last) + 3, 7);
  const at = (day: string | null | undefined) =>
    day ? Math.max(0, Math.min(1, daysBetween(start, day) / span)) : null;
  return {
    start,
    end: last,
    today: at(today) ?? 0,
    committed: at(project.target),
    likelyFrom: at(project.p50 ?? project.p85),
    likelyTo: at(project.p85),
    team: at(project.team_date),
    marks: project.marks
      .filter((mark) => mark.day >= start)
      .map((mark, index) => ({
        at: at(mark.day) ?? 0,
        pushed: mark.pushed,
        label: markWords(mark),
        key: `${mark.day}:${mark.kind}:${index}`,
      })),
    done: project.total > 0 ? project.done / project.total : null,
  };
}

/** The bar said in words, for its aria-label: what a reader of the picture sees. */
export function barSummary(project: PortfolioProjectDto): string {
  const parts = [
    project.total > 0
      ? `${project.done} of ${project.total} requirements done`
      : "No requirements counted",
    project.target ? `committed ${formatDay(project.target)}` : "no committed date",
    project.p85 ? `likely by ${formatDay(project.p85)}` : "not forecast yet",
  ];
  const pushed = project.marks.filter((mark) => mark.pushed).length;
  const other = project.marks.length - pushed;
  if (pushed) parts.push(`${pushed} ${pushed === 1 ? "change" : "changes"} pushed the date`);
  if (other) parts.push(`${other} other ${other === 1 ? "change" : "changes"}`);
  return `${project.name}: ${parts.join(", ")}.`;
}

export type ProgrammeGroup = {
  id: string;
  name: string;
  counts: VerdictCountsDto | null;
  projects: PortfolioProjectDto[];
};

/**
 * The projects grouped under their programme, for the readers of programmes
 * (manager, executive, admin): each group where its worst project first
 * appears, so the order stays worst first; projects in no programme last.
 */
export function byProgramme(
  projects: readonly PortfolioProjectDto[],
  programs: readonly PortfolioProgramDto[],
): ProgrammeGroup[] {
  const counts = new Map(programs.map((program) => [program.id, program.counts]));
  const groups = new Map<string, ProgrammeGroup>();
  for (const project of projects) {
    const id = project.program_id ?? "";
    let group = groups.get(id);
    if (!group) {
      group = {
        id,
        name: project.program_name ?? "In no programme",
        counts: counts.get(id) ?? null,
        projects: [],
      };
      groups.set(id, group);
    }
    group.projects.push(project);
  }
  const ordered = [...groups.values()];
  return [...ordered.filter((group) => group.id), ...ordered.filter((group) => !group.id)];
}

/** A programme's counts in one line: "1 off track · 2 at risk · 3 on track". */
export function programmeLine(counts: VerdictCountsDto | null): string | null {
  if (!counts) return null;
  const parts: [number, string][] = [
    [counts.off_track ?? 0, "off track"],
    [counts.at_risk ?? 0, "at risk"],
    [counts.no_date ?? 0, "no committed date"],
    [counts.not_enough_data ?? 0, "not enough history"],
    [counts.on_track ?? 0, "on track"],
    [counts.done ?? 0, "done"],
  ];
  const said = parts.filter(([count]) => count > 0).map(([count, words]) => `${count} ${words}`);
  return said.length ? said.join(" · ") : null;
}

/** The page's filters, kept in the URL: `?q=`, `?verdict=` (repeated) and `?program=`. */
export function filtersOf(search: URLSearchParams): {
  q: string;
  verdicts: VerdictFilter[];
  program: string | null;
} {
  const known = new Set<string>(Object.keys(TILE_LABELS));
  return {
    q: search.get("q") ?? "",
    verdicts: search.getAll("verdict").filter((value): value is VerdictFilter => known.has(value)),
    program: search.get("program"),
  };
}

/** Toggle one verdict filter, keeping every other parameter (the viewing day too). */
export function toggledVerdict(search: URLSearchParams, filter: VerdictFilter): URLSearchParams {
  const next = new URLSearchParams(search);
  const on = next.getAll("verdict");
  next.delete("verdict");
  const kept = on.includes(filter) ? on.filter((value) => value !== filter) : [...on, filter];
  kept.forEach((value) => next.append("verdict", value));
  return next;
}
