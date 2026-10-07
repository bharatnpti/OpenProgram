import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse, Rag } from "../../api/schema";
import { usePods, useProgramChoice, useProjects, useWorkstreams } from "../../app/directory";
import { useRole } from "../../app/role";
import { scopeToProgram } from "../../app/scope";
import { useShownDay } from "../../app/viewingDate";
import { PanelState } from "../../components/PanelState";
import { ChipPicker, Greeting, Panel, RagDot, Row, Sparkline } from "../../components/ui/Bits";
import { formatDate, formatTime } from "../../lib/format";
import { ragSeverity } from "../../lib/status";
import { cn } from "../../lib/utils";
import {
  checkinsLine,
  greetingWord,
  plural,
  signalAge,
  signalKindLabel,
  todayEyebrow,
} from "../../lib/words";
import {
  momentum,
  noPodTiles,
  signalHref,
  tileKey,
  tileColours,
  tileReasons,
  type NoPodTile,
  type TileReason,
} from "./heat";

const HEAT_COLUMNS = 4;
const SIGNALS_SHOWN = 5;

const HERO: Record<Rag, string> = {
  red: "bg-rag-red-bg text-rag-red",
  amber: "bg-rag-amber-bg text-rag-amber-deep",
  green: "bg-rag-green-bg text-rag-green",
  unknown: "bg-grey-fill text-grey-body",
};
const TILE: Record<Rag, string> = {
  red: "bg-rag-red-bg text-rag-red",
  amber: "bg-rag-amber-bg text-rag-amber",
  green: "bg-rag-green-bg text-rag-green",
  unknown: "bg-rag-unknown-bg text-rag-unknown",
};

/**
 * The portfolio read shared by manager, executive and admin: a one-line
 * verdict with its reason, 30-day momentum, the newest executive brief,
 * heat for projects, workstreams and pods (worst first, each saying why), the
 * people in no team, and the oldest risks. A tenant with several programs shows
 * one at a time: pick it in the verdict area; each chip carries the program's
 * colour and the screen opens on the worst.
 */
export function PortfolioToday() {
  const shownDay = useShownDay();
  const { roleLabel, canReadPortfolio } = useRole();
  const { query: programsQuery, programs, program, choose } = useProgramChoice();
  const programId = program?.id ?? "";
  const projects = useProjects();
  const workstreams = useWorkstreams();
  const pods = usePods();

  const attention = useQuery({
    queryKey: ["portfolio", "attention", programId],
    queryFn: () =>
      apiClient.portfolioAttention(
        undefined,
        programId,
        Intl.DateTimeFormat().resolvedOptions().timeZone,
      ),
    enabled: Boolean(programId) && canReadPortfolio,
  });
  const trend = useQuery({
    queryKey: ["portfolio", "trend", programId],
    queryFn: () => apiClient.nodeTrend("program", programId, { windowDays: 30 }),
    enabled: Boolean(programId) && canReadPortfolio,
  });
  // The heat map's cells carry each node's reason, and the people in no team.
  const heatmap = useQuery({
    queryKey: ["portfolio", "heatmap", programId],
    queryFn: () => apiClient.portfolioHeatmap(undefined, programId),
    enabled: Boolean(programId) && canReadPortfolio,
  });
  const brief = useQuery({
    queryKey: ["briefs", "exec", 1],
    queryFn: () => apiClient.personaBriefs("exec", 1),
  });

  const a = attention.data;
  const m = momentum(trend.data?.points);
  const newest = brief.data?.briefs[0];
  const scoped = scopeToProgram(programId, programs.length, {
    projects: projects.data ?? [],
    workstreams: workstreams.data ?? [],
    pods: pods.data ?? [],
  });
  const heat = [
    { label: "Projects", kind: "project", items: scoped.projects },
    { label: "Workstreams", kind: "workstream", items: scoped.workstreams },
    { label: "Pods", kind: "pod", items: scoped.pods },
  ].filter((row) => row.kind !== "workstream" || row.items.length > 0);
  const reasons = tileReasons(heatmap.data?.cells);
  const colours = tileColours(heatmap.data?.cells);
  const noPod = noPodTiles(heatmap.data?.cells, HEAT_COLUMNS);

  return (
    <>
      <Greeting
        eyebrow={todayEyebrow(program?.name, shownDay)}
        title={`${greetingWord()}, ${roleLabel}`}
        sub="A portfolio-wide read on delivery health, momentum, and what changed."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
        {programs.length > 1 ? (
          <ChipPicker
            label="Program"
            value={programId}
            onChange={choose}
            options={programs.map((item) => ({ value: item.id, label: item.name, rag: item.rag }))}
            note={`${programs.length} programs · worst first`}
          />
        ) : null}
        <PanelState
          locked={!canReadPortfolio}
          needs="a manager, executive or admin"
          isLoading={programsQuery.isLoading || attention.isLoading}
          error={programsQuery.error ?? attention.error}
          isEmpty={!programsQuery.isLoading && !program}
          emptyText="No program is configured yet. An admin adds one under Admin → Entities."
        >
          {a ? (
            <section className={cn("rounded-3xl p-6", HERO[a.rag])}>
              <p className="flex items-center gap-3 text-[24px] font-extrabold text-balance">
                <RagDot rag={a.rag} className="h-3 w-3" />
                {a.headline}
              </p>
              {a.detail ? <p className="mt-2 text-[15px] font-medium">{a.detail}</p> : null}
              <p className="mt-3 text-[13px] opacity-80">
                {checkinsLine(
                  a.checkins,
                  a.checkins.first_asked_at ? formatTime(a.checkins.first_asked_at) : null,
                )}
              </p>
            </section>
          ) : null}
        </PanelState>

        <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
          <Panel title="Momentum" note={`30 days · ${m.label}`}>
            <PanelState
              locked={!canReadPortfolio}
              needs="a manager, executive or admin"
              isLoading={trend.isLoading}
              error={trend.error}
            >
              <Sparkline values={m.values} label={`Program health over 30 days: ${m.label}`} />
              <p className="mt-2 text-[12px] text-grey-secondary">
                Measured only over days that reported a status.
              </p>
            </PanelState>
          </Panel>
          <Panel
            title="Executive brief"
            note={
              <Link to="/coordination?brief=exec" className="font-bold">
                All briefs
              </Link>
            }
          >
            <PanelState
              needs="a scrum master, product owner, manager, executive or admin"
              isLoading={brief.isLoading}
              error={brief.error}
              isEmpty={!newest}
              emptyText="No executive brief has been generated yet. It is written weekly from the week's facts."
            >
              {newest ? (
                <div className="text-[14px] text-grey-body">
                  <p className="font-bold text-ink">{newest.title}</p>
                  {newest.bullets && newest.bullets.length > 0 ? (
                    <ul className="mt-2 grid list-disc gap-1 pl-5">
                      {newest.bullets.map((b) => (
                        <li key={b}>{b}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className="mt-2">{newest.body}</p>
                  )}
                  <p className="mt-2 text-[12px] text-grey-secondary">
                    {formatDate(newest.generated_at)} · from{" "}
                    {plural(newest.sources.length, "source", "sources")}
                  </p>
                </div>
              ) : null}
            </PanelState>
          </Panel>
        </div>

        <Panel
          title="Portfolio heat"
          note="worst first · click a tile to open it in Delivery · hover for every reason"
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
              />
            ))}
            {noPod.tiles.length > 0 ? <NoPodRow tiles={noPod.tiles} total={noPod.total} /> : null}
          </div>
          {heatmap.isError ? (
            <p className="mt-3 text-[12px] text-grey-secondary">
              The reasons under each tile could not be loaded, so only the colours show.
            </p>
          ) : null}
        </Panel>

        <Panel
          title="Oldest open risks"
          note={
            <Link to="/signals" className="font-bold">
              All signals
            </Link>
          }
        >
          <PanelState
            locked={!canReadPortfolio}
            needs="a manager, executive or admin"
            isLoading={attention.isLoading}
            error={attention.error}
            isEmpty={(a?.signals ?? []).length === 0}
            emptyText="Nothing needs attention right now."
          >
            {programs.length > 1 ? (
              <p className="mb-1 text-[12px] text-grey-secondary">
                Risks are read across the whole portfolio, not one program.
              </p>
            ) : null}
            <ul>
              {(a?.signals ?? []).slice(0, SIGNALS_SHOWN).map((s, i) => (
                <Row
                  key={`${s.kind}-${i}`}
                  rag={s.severity}
                  title={
                    <Link to={signalHref(s.link)} className="text-ink no-underline hover:underline">
                      {s.title}
                    </Link>
                  }
                  meta={signalKindLabel(s.kind)}
                  right={signalAge(s.age_days)}
                />
              ))}
            </ul>
          </PanelState>
        </Panel>
      </div>
    </>
  );
}

/** A row of heat, `HEAT_COLUMNS` wide: label on the left, then a tile per node, worst first. */
function HeatRow({
  label,
  kind,
  items,
  reasons,
  colours,
}: {
  label: string;
  kind: string;
  items: DirectoryItemResponse[];
  reasons: Map<string, TileReason>;
  colours: Map<string, Rag>;
}) {
  const colourOf = (item: DirectoryItemResponse): Rag =>
    colours.get(tileKey(kind, item.id)) ?? item.rag ?? "unknown";
  const ranked = [...items].sort(
    (a, b) => ragSeverity(colourOf(b)) - ragSeverity(colourOf(a)) || a.name.localeCompare(b.name),
  );
  const shown = ranked.slice(0, HEAT_COLUMNS);
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] items-start gap-2 sm:grid-cols-[110px_minmax(0,1fr)]">
      <p className="pt-2 text-[13px] font-bold text-grey-secondary">
        {label}
        {ranked.length > HEAT_COLUMNS ? (
          <span className="block text-[11px] font-medium">
            worst {HEAT_COLUMNS} of {ranked.length}
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
            return (
              <li key={item.id}>
                <Link
                  to={`/delivery/${kind}/${encodeURIComponent(item.id)}`}
                  title={why?.tooltip}
                  className={cn(
                    "block min-h-[76px] rounded-2xl px-3 py-3 no-underline hover:shadow-op-hover",
                    TILE[rag],
                  )}
                >
                  <TileText name={item.name} colour={rag} why={why?.reason} />
                </Link>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}

/**
 * People in no pod, project or program (an executive, or someone not yet
 * placed): their own check-in, which no team's colour counts and the verdict
 * never reads.
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
          <li key={tile.id}>
            <div
              title={[tile.name, ...tile.reasons.map((line) => `• ${line}`)].join("\n")}
              className={cn("min-h-[76px] rounded-2xl px-3 py-3", TILE[tile.rag])}
            >
              <TileText
                name={tile.name}
                colour={tile.reason ? tile.rag : tile.state}
                why={tile.reason ?? undefined}
              />
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** A tile's name, its colour, and the few words that say why. */
function TileText({
  name,
  colour,
  why,
}: {
  name: string;
  colour: string;
  why: string | undefined;
}) {
  return (
    <>
      <span className="block truncate text-[14px] font-bold">{name}</span>
      <span className="mt-1 block text-[11px] font-extrabold uppercase tracking-wider">
        {colour}
      </span>
      {why ? (
        <span className="mt-0.5 block line-clamp-2 text-[12px] font-semibold">{why}</span>
      ) : null}
    </>
  );
}
