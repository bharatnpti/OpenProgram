import { Link } from "react-router-dom";

import type { DirectoryItemResponse, ScopeDeliveryResponse } from "../../api/schema";
import { dailyPath } from "../../app/links";
import { useRole } from "../../app/role";
import { verdictChip } from "../../components/ui/dateStripWords";
import { RagChip } from "../../components/ui/RagChip";
import { PERSON_KEY_WORDS } from "../../lib/words";
import { Trail } from "../delivery/NodeBits";
import type { ProjectOption } from "./projectChoice";
import { ProjectSwitcher } from "./ProjectSwitcher";

/**
 * The top of the Project page: where it sits (the programme), the project's
 * name, which opens the switcher, the date verdict as its one pill (D92), the
 * day report, then one line of who owns it and one of today's health in words
 * ("Today: CHK-102 blocked"), never as a second colour. An executive gets
 * neither line, and no day report link: no names (D87, D103).
 */
export function ProjectHeader({
  projectId,
  name,
  project,
  programName,
  scope,
  health,
  names,
  choice,
}: {
  projectId: string;
  name: string;
  project: DirectoryItemResponse | undefined;
  programName: string | null;
  /** The delivery read's scope the page is about (the project, or the release picked). */
  scope: ScopeDeliveryResponse | undefined;
  /** Today's health in words, from the project's progress; null when not the person's to read. */
  health: string | null;
  /** Owner and team names may be shown (not an executive). */
  names: boolean;
  choice: { options: ProjectOption[]; programs: { id: string; name: string }[]; recent: string[] };
}) {
  const daily = useRole().access.reports.daily;
  const pill = scope ? verdictChip(scope) : null;
  const people = names
    ? (project?.people ?? [])
        .filter((person) => person.name)
        .map((person) => `${PERSON_KEY_WORDS[person.key] ?? person.key} ${person.name}`)
    : [];
  return (
    <header className="mb-6 grid gap-2">
      {programName ? <Trail steps={[{ label: programName, to: null }]} /> : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <ProjectSwitcher
          current={{ id: projectId, name }}
          options={choice.options}
          programs={choice.programs}
          recent={choice.recent}
        />
        {pill ? (
          <RagChip tone={pill.tone} dot className="h-[26px] px-3 text-[12.5px]">
            {pill.label}
          </RagChip>
        ) : null}
        {daily ? (
          <Link
            to={dailyPath(projectId)}
            className="ml-auto inline-flex h-9 items-center rounded-full border border-ink px-4 text-[13px] font-bold text-ink no-underline hover:bg-grey-fill"
          >
            Daily report
          </Link>
        ) : null}
      </div>
      {people.length > 0 ? (
        <p className="text-[13.5px] text-grey-body">{people.join(" · ")}</p>
      ) : null}
      {health ? <p className="text-[13.5px] text-grey-body">Today: {health}</p> : null}
    </header>
  );
}
