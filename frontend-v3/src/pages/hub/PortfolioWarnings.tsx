import { useSearchParams } from "react-router-dom";

import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { TabIntro } from "../../features/hub/TabIntro";
import { HeatView } from "../../features/portfolio/HeatView";
import { ProgrammeHealth } from "../../features/portfolio/ProgrammeHealth";
import { ProgrammePicker } from "../../features/portfolio/ProgrammePicker";
import { useProgrammes } from "../../features/portfolio/useProgrammes";
import { warningsCountLine } from "../../features/portfolio/programmeWords";
import { WarningsByProject } from "../../features/portfolio/WarningsByProject";
import { usePortfolioWarnings } from "../../features/warnings/queries";
import { WarningGroup } from "../../features/warnings/WarningGroups";
import { shownGroups } from "../../features/warnings/warningWords";
import { formatDay } from "../../lib/format";
import { cn } from "../../lib/utils";

type View = "kind" | "project" | "heat";

const VIEW_LABELS: Record<View, string> = {
  kind: "By kind",
  project: "By project",
  heat: "Heat",
};

/**
 * All projects › Early warnings (`/portfolio/warnings`): what Jira and Git
 * say, whatever anyone reports, in one read across the projects the person
 * reads (`GET /portfolio/warnings`). For the readers of programmes (manager,
 * executive, admin) it opens on the programme's health today: its headline,
 * the day's check-ins, what sets its colour and what rolls up into it. Then the
 * warnings by kind (`?view=kind`, the default), by project with what each
 * owner says (`?view=project`), or, for the readers of programmes, as heat
 * (`?view=heat`): today's health of every project, workstream and pod, and the
 * people in no pod. Every warning has an owner; findings attach to work items
 * and nobody is ranked. A scrum master's and a product owner's are their own
 * projects', by the server's rule.
 */
export function PortfolioWarnings() {
  const { access } = useRole();
  const health = access.hub.portfolioHealth;
  const [search, setSearch] = useSearchParams();
  const views: View[] = health ? ["kind", "project", "heat"] : ["kind", "project"];
  const asked = search.get("view") as View | null;
  const view: View = asked && views.includes(asked) ? asked : "kind";
  const { programmes } = useProgrammes();
  const askedProgramme = search.get("program");
  const programme = programmes.find((item) => item.id === askedProgramme) ?? programmes[0];
  const warnings = usePortfolioWarnings();
  const data = warnings.data;
  const seenSince = data?.seen_since;
  const set = (key: string, value: string | null) =>
    setSearch(
      (current) => {
        const next = new URLSearchParams(current);
        if (value === null) next.delete(key);
        else next.set(key, value);
        return next;
      },
      { replace: true },
    );

  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-6">
      <TabIntro
        tab="warnings"
        meta={
          <>
            What Jira and Git say, whatever anyone reports. Every warning has an owner; findings
            attach to work items, and nobody is ranked.{data ? ` ${warningsCountLine(data)}` : ""}
            {seenSince
              ? ` Ages of disagreements count from ${formatDay(seenSince)}, when they were first recorded.`
              : ""}
          </>
        }
      />
      {health && programme ? (
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
          <ProgrammePicker
            id="warnings-programme"
            programmes={programmes}
            value={programme.id}
            onChange={(id) => set("program", id)}
          />
          <ProgrammeHealth key={programme.id} programId={programme.id} name={programme.name} />
        </div>
      ) : null}
      <div
        role="group"
        aria-label="Show the warnings"
        className="inline-flex w-fit max-w-full flex-wrap rounded-full border border-grey-border bg-(--op-viz-surface) p-0.5"
      >
        {views.map((item) => (
          <button
            key={item}
            type="button"
            aria-pressed={view === item}
            onClick={() => set("view", item === "kind" ? null : item)}
            className={cn(
              "min-h-11 rounded-full px-4 text-[13px] font-bold sm:min-h-9",
              view === item ? "bg-ink text-white" : "text-grey-body hover:text-ink",
            )}
          >
            {VIEW_LABELS[item]}
          </button>
        ))}
      </div>
      {view === "heat" && programme ? (
        <HeatView key={programme.id} programId={programme.id} programCount={programmes.length} />
      ) : (
        <PanelState
          isLoading={warnings.isLoading}
          error={warnings.error}
          onRetry={() => void warnings.refetch()}
        >
          {data ? (
            view === "project" ? (
              <WarningsByProject view={data} />
            ) : (
              <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
                {shownGroups(data).map((group) => (
                  <WarningGroup key={group.kind} group={group} seenSince={seenSince} />
                ))}
              </div>
            )
          ) : null}
        </PanelState>
      )}
    </div>
  );
}
