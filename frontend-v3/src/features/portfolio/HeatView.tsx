import { Link } from "react-router-dom";

import type { DirectoryItemResponse, Rag } from "../../api/schema";
import { usePods, useProjects, useWorkstreams } from "../../app/directory";
import { podTabPath, projectTabPath, workstreamPath } from "../../app/links";
import { useRole } from "../../app/role";
import { scopeToProgram } from "../../app/scope";
import { PanelState } from "../../components/PanelState";
import { formatDay } from "../../lib/format";
import { readState } from "../../lib/readState";
import { cn } from "../../lib/utils";
import { usePodOpens, usePortfolioIndex } from "../hub/useHub";
import {
  colourWord,
  heatTiles,
  noPodTiles,
  rankByHeat,
  tileColours,
  tileKey,
  tileReasons,
  tileWeights,
  type NoPodTile,
  type TileReason,
} from "./heat";
import { useHeatmap } from "./useHeatmap";
import { VizPanel } from "./VizPanel";

const HEAT_COLUMNS = 4;

const TILE: Record<Rag, string> = {
  red: "bg-rag-red-bg text-rag-red",
  amber: "bg-rag-amber-bg text-rag-amber",
  green: "bg-rag-green-bg text-rag-green",
  unknown: "bg-rag-unknown-bg text-rag-unknown",
};

/**
 * Heat, on All projects › Early warnings: today's health of every project,
 * workstream (only those in use) and pod of the programme, worst first, each
 * tile with the few words that say why, a project's committed date, and every
 * reason on hover. Then the people in no pod, whom no team's colour counts, for
 * a manager and an admin. Today's health, not the date: the one place a health
 * colour fills a tile. A tile opens its project or pod at Early warnings, a
 * workstream its project's row; an executive's pod tile opens only for a pod
 * whose level the server offers them (five people or more).
 */
export function HeatView({ programId, programCount }: { programId: string; programCount: number }) {
  const { access } = useRole();
  const canReadPortfolio = access.hub.portfolioHealth;
  // A reader who sees no names (an executive): no people in no pod, and a pod's
  // own level only for the pods the server lists for them (five people or more).
  const countsOnly = access.hub.countsOnly;
  const projects = useProjects();
  const workstreams = useWorkstreams();
  const pods = usePods();
  const heatmap = useHeatmap(programId, canReadPortfolio);
  // The committed dates, and the pods each project lists for this reader: the
  // bulk read of kept rows the hub shares (never a live forecast per project).
  const index = usePortfolioIndex();
  const opens = usePodOpens();
  const directory = readState(projects, workstreams, pods);
  const scoped = scopeToProgram(programId, programCount, {
    projects: projects.data ?? [],
    workstreams: (workstreams.data ?? []).filter((item) => item.in_use !== false),
    pods: pods.data ?? [],
  });
  const rows = index.data?.projects ?? [];
  const due = new Map(
    rows.flatMap((project) => (project.target ? [[project.id, project.target] as const] : [])),
  );
  const listedOn = new Map<string, string>();
  rows.forEach((project) =>
    project.pods.forEach((pod) => {
      if (!listedOn.has(pod.id)) listedOn.set(pod.id, project.id);
    }),
  );
  const cells = heatmap.data?.cells;
  const reasons = tileReasons(cells);
  const colours = tileColours(cells);
  const weights = tileWeights(cells);
  const noPod = countsOnly ? null : noPodTiles(cells, HEAT_COLUMNS);
  const inProgramme = new Set(scoped.projects.map((project) => project.id));
  const firstProject = (item: DirectoryItemResponse) =>
    item.project_ids.find((id) => inProgramme.has(id)) ?? item.project_ids[0] ?? null;

  const linkOf = (kind: string, item: DirectoryItemResponse): string | null => {
    if (kind === "project") return projectTabPath(item.id, "warnings");
    if (kind === "pod") {
      // A counts-only reader opens a pod only on a project whose row lists it.
      if (!opens(item.id)) return null;
      const projectId = countsOnly ? (listedOn.get(item.id) ?? null) : firstProject(item);
      return projectId ? podTabPath(projectId, item.id, "warnings") : null;
    }
    const projectId = firstProject(item);
    return projectId ? workstreamPath(item.id, [projectId]) : null;
  };

  const heat = [
    { label: "Projects", kind: "project", items: scoped.projects },
    { label: "Workstreams", kind: "workstream", items: scoped.workstreams },
    { label: "Pods", kind: "pod", items: scoped.pods },
  ].filter((row) => row.kind !== "workstream" || row.items.length > 0);

  return (
    <VizPanel
      title="Heat"
      titleId="heat-h"
      note="Today's health, not the date · worst first · hover a tile for every reason"
    >
      <PanelState
        isLoading={directory.isLoading || heatmap.isLoading}
        error={directory.error ?? heatmap.error}
        onRetry={() => void heatmap.refetch()}
      >
        <div className="mt-2 grid grid-cols-[minmax(0,1fr)] gap-3">
          {heat.map((row) => (
            <HeatRow
              key={row.kind}
              label={row.label}
              kind={row.kind}
              items={row.items}
              reasons={reasons}
              colours={colours}
              weights={weights}
              due={row.kind === "project" ? due : undefined}
              linkOf={linkOf}
            />
          ))}
          {noPod && noPod.tiles.length > 0 ? (
            <NoPodRow tiles={noPod.tiles} total={noPod.total} />
          ) : null}
        </div>
      </PanelState>
    </VizPanel>
  );
}

/** A row of heat, `HEAT_COLUMNS` wide: its label, then a tile per node, worst first. */
function HeatRow({
  label,
  kind,
  items,
  reasons,
  colours,
  weights,
  due,
  linkOf,
}: {
  label: string;
  kind: string;
  items: DirectoryItemResponse[];
  reasons: Map<string, TileReason>;
  colours: Map<string, Rag>;
  weights: Map<string, number>;
  /** A project's committed date, for "Due Fri 30 Oct" on its tile. */
  due?: Map<string, string>;
  linkOf: (kind: string, item: DirectoryItemResponse) => string | null;
}) {
  const colourOf = (item: DirectoryItemResponse): Rag =>
    colours.get(tileKey(kind, item.id)) ?? item.rag ?? "unknown";
  const ranked = rankByHeat(items, colourOf, (item) => weights.get(tileKey(kind, item.id)) ?? 0);
  const { shown, hidden, hiddenAsBad } = heatTiles(ranked, colourOf, HEAT_COLUMNS);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-2 sm:grid-cols-[110px_minmax(0,1fr)]">
      <p className="pt-2 text-[13px] font-bold text-grey-secondary">
        {label}
        {hidden > 0 ? (
          <span className="block text-[11px] font-medium">
            worst {shown.length} of {ranked.length}
            {hiddenAsBad ? " · the rest are as bad" : ""}
          </span>
        ) : null}
      </p>
      {shown.length === 0 ? (
        <p className="pt-2 text-[13px] text-grey-secondary">None configured.</p>
      ) : (
        <ul className="grid grid-cols-2 gap-2 lg:grid-cols-4">
          {shown.map((item) => {
            const rag = colourOf(item);
            const why = reasons.get(tileKey(kind, item.id));
            const to = linkOf(kind, item);
            const text = (
              <TileText
                name={item.name}
                colour={colourWord(rag)}
                why={why?.reason}
                due={due?.get(item.id)}
              />
            );
            return (
              <li key={item.id} className="min-w-0">
                {to ? (
                  <Link
                    to={to}
                    title={why?.tooltip}
                    className={cn(
                      "block min-h-[76px] rounded-2xl px-3 py-3 no-underline hover:shadow-op-hover",
                      TILE[rag],
                    )}
                  >
                    {text}
                  </Link>
                ) : (
                  <div
                    title={why?.tooltip}
                    className={cn("min-h-[76px] rounded-2xl px-3 py-3", TILE[rag])}
                  >
                    {text}
                  </div>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * People in no pod, project or programme (an executive, or someone not yet
 * placed): their own check-in, which no team's colour counts and the verdict
 * never reads. Their names are also on People & roles › Not in any pod.
 */
function NoPodRow({ tiles, total }: { tiles: NoPodTile[]; total: number }) {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-2 sm:grid-cols-[110px_minmax(0,1fr)]">
      <p className="pt-2 text-[13px] font-bold text-grey-secondary">
        No pod
        <span className="block text-[11px] font-medium">
          {total > tiles.length ? `worst ${tiles.length} of ${total}` : "not in team colours"}
        </span>
      </p>
      <ul className="grid grid-cols-2 gap-2 lg:grid-cols-4">
        {tiles.map((tile) => (
          <li key={tile.id} className="min-w-0">
            <div
              title={[tile.name, ...tile.reasons.map((line) => `• ${line}`)].join("\n")}
              className={cn("min-h-[76px] rounded-2xl px-3 py-3", TILE[tile.rag])}
            >
              <TileText
                name={tile.name}
                colour={tile.reason ? colourWord(tile.rag) : tile.state}
                why={tile.reason ?? undefined}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A tile's name, its colour in words, and the few words that say why. */
function TileText({
  name,
  colour,
  why,
  due,
}: {
  name: string;
  colour: string;
  why: string | undefined;
  due?: string;
}) {
  return (
    <>
      <span className="block truncate text-[14px] font-bold">{name}</span>
      <span className="mt-1 block text-[11px] font-extrabold uppercase tracking-wider">
        {colour}
        {due ? (
          <span className="font-bold normal-case tracking-normal"> · Due {formatDay(due)}</span>
        ) : null}
      </span>
      {why ? (
        <span className="mt-0.5 block line-clamp-2 text-[12px] font-semibold">{why}</span>
      ) : null}
    </>
  );
}
