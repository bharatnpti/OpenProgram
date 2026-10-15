import { useState } from "react";
import { Link } from "react-router-dom";

import type { PortfolioWarningsResponse } from "../../api/schema";
import { RagDot } from "../../components/ui/Bits";
import { RagChip } from "../../components/ui/RagChip";
import { projectTabPath } from "../../app/links";
import { ItemPods, OwnerSaysFold } from "../warnings/WarningGroups";
import { WARNING_LINKS } from "../warnings/warningLinks";
import { ageWords, itemLine, ownerWords } from "../warnings/warningWords";
import {
  byProject,
  countedApart,
  isDrift,
  KIND_WORDS,
  projectCount,
  type ProjectRow,
} from "./byProject";
import { VizPanel } from "./VizPanel";

/** The warnings a project lists before "Show all". */
const SHOWN = 5;

/**
 * Early warnings grouped by project, the worst project first: each warning
 * with its kind (drift marked), its owner and, where the reader sees the owner,
 * what the owner last said in their own words, and its age. The project's name
 * opens its Early warnings, where the list is said in full with its questions.
 */
export function WarningsByProject({ view }: { view: PortfolioWarningsResponse }) {
  const projects = byProject(view);
  const apart = countedApart(view);
  if (projects.length === 0) {
    return (
      <p className="text-[14px] text-grey-secondary">
        {apart ?? "Nothing open: Jira and Git agree with what was reported."}
      </p>
    );
  }
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-4">
      {projects.map((project) => (
        <ProjectPanel key={project.projectId} project={project} seenSince={view.seen_since} />
      ))}
      {apart ? <p className="text-[13px] text-grey-secondary">{apart}</p> : null}
    </div>
  );
}

function ProjectPanel({
  project,
  seenSince,
}: {
  project: ReturnType<typeof byProject>[number];
  seenSince: string | null | undefined;
}) {
  const [all, setAll] = useState(false);
  const items = all ? project.items : project.items.slice(0, SHOWN);
  const headingId = `by-project-${project.projectId}`;
  return (
    <VizPanel
      titleId={headingId}
      title={
        <span className="inline-flex min-w-0 items-center gap-2">
          <RagDot rag={project.worst} />
          <Link
            to={projectTabPath(project.projectId, "warnings")}
            className="min-w-0 text-ink no-underline hover:underline"
          >
            {project.name}
          </Link>
        </span>
      }
      note={projectCount(project)}
    >
      <ul aria-labelledby={headingId}>
        {items.map((item) => (
          <ProjectWarningRow key={item.id} item={item} seenSince={seenSince} />
        ))}
      </ul>
      {project.items.length > SHOWN ? (
        <button
          type="button"
          onClick={() => setAll((value) => !value)}
          className="mt-2 min-h-11 text-[13px] font-bold text-ink underline sm:min-h-0"
          aria-expanded={all}
        >
          {all ? "Show fewer" : `Show all ${project.items.length}`}
        </button>
      ) : null}
    </VizPanel>
  );
}

function ProjectWarningRow({
  item,
  seenSince,
}: {
  item: ProjectRow;
  seenSince: string | null | undefined;
}) {
  return (
    <li className="flex min-w-0 items-start gap-3 border-t border-grey-border py-3 first:border-t-0">
      <RagDot rag={item.severity} className="mt-1.5" />
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-[14px]">
          <span className="font-extrabold text-ink break-words">
            {item.evidence_url ? (
              <a href={item.evidence_url} target="_blank" rel="noreferrer">
                {item.key}
              </a>
            ) : (
              item.key
            )}
          </span>
          <span className="min-w-0 text-grey-body break-words">{itemLine(item)}</span>
        </p>
        <p className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-[12px] text-grey-secondary">
          <RagChip tone="neutral" className="h-5 px-2 text-[11px]">
            {KIND_WORDS[item.group]}
          </RagChip>
          {isDrift(item.group) ? (
            <RagChip tone="neutral" className="h-5 px-2 text-[11px]">
              drift
            </RagChip>
          ) : null}
          <span>
            {ownerWords(item, item.group)}
            <ItemPods item={item} links={WARNING_LINKS} />
            {item.watermelon ? " · owner says on track" : ""}
          </span>
        </p>
        <OwnerSaysFold item={item} />
      </div>
      <span className="flex-none text-right text-[12px] font-bold text-grey-secondary tabular-nums">
        {ageWords(item, seenSince)}
      </span>
    </li>
  );
}
