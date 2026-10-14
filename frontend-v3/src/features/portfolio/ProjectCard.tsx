import { Link } from "react-router-dom";

import type { PortfolioPodDto, PortfolioProjectDto } from "../../api/schema";
import { projectPath } from "../../app/links";
import { RagChip } from "../../components/ui/RagChip";
import { formatDay } from "../../lib/format";
import { ProjectBar } from "./ProjectBar";
import {
  healthLine,
  lateWords,
  podDateWords,
  releaseChip,
  threeDates,
  verdictPill,
} from "./portfolioWords";

/**
 * One project on Will we make the date?: its name and release, the verdict as
 * the one pill, the reason with today's health in words, the three dates
 * side by side, the bar, done per pod, and the way to the project's page.
 */
export function ProjectCard({
  project,
  today,
  shownDay,
  viewingPast,
}: {
  project: PortfolioProjectDto;
  today: string;
  shownDay: string;
  viewingPast: boolean;
}) {
  const pill = verdictPill(project);
  const release = releaseChip(project.releases);
  const late = lateWords(project);
  const headingId = `portfolio-${project.id}`;
  return (
    <article
      aria-labelledby={headingId}
      className="grid min-w-0 grid-cols-[minmax(0,1fr)] gap-4 rounded-3xl border border-grey-border bg-(--op-viz-surface) p-5 max-sm:rounded-[20px] max-sm:p-4"
    >
      <header className="flex min-w-0 flex-wrap items-start justify-between gap-x-3 gap-y-2">
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          <h3 id={headingId} className="min-w-0 text-[17px] font-extrabold break-words">
            <Link to={projectPath(project.id)} className="text-ink no-underline hover:underline">
              {project.name}
            </Link>
          </h3>
          {release ? (
            <span className="rounded-full border border-grey-border px-2.5 py-0.5 text-[12px] font-bold text-grey-body">
              {release}
            </span>
          ) : null}
        </div>
        <RagChip tone={pill.tone} className="h-6 px-2.5 text-[12px]">
          {pill.label}
        </RagChip>
      </header>

      <p className="text-[14px] text-grey-body">
        <span className="font-bold text-ink">{project.reason}</span>{" "}
        <span className="text-grey-secondary">· {healthLine(project, shownDay, viewingPast)}</span>
      </p>

      <dl className="grid grid-cols-3 gap-3 max-sm:grid-cols-[minmax(0,1fr)] max-sm:gap-1.5">
        {threeDates(project, today).map((item) => (
          <div
            key={item.label}
            className="min-w-0 max-sm:flex max-sm:items-baseline max-sm:justify-between max-sm:gap-3"
          >
            <dt className="text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
              {item.label}
            </dt>
            <dd className="text-[14px] font-bold text-ink tabular-nums">
              {item.value}
              {item.hint ? (
                <span className="ml-1.5 text-[12px] font-normal text-grey-secondary">
                  {item.hint}
                </span>
              ) : null}
            </dd>
          </div>
        ))}
      </dl>

      <ProjectBar project={project} today={today} />

      {project.pods.length > 0 ? <PodRows pods={project.pods} /> : null}

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-grey-border pt-3 text-[13px]">
        <span className="text-grey-secondary">
          {late ?? (project.kept_on ? `Forecast kept ${formatDay(project.kept_on)}` : "")}
        </span>
        <Link to={projectPath(project.id)} className="font-bold text-ink">
          Open <span aria-hidden>→</span>
          <span className="sr-only"> {project.name}</span>
        </Link>
      </footer>
    </article>
  );
}

/** Done per pod: a row each, its share of the project's requirements done, and its verdict. */
function PodRows({ pods }: { pods: PortfolioPodDto[] }) {
  return (
    <table className="w-full table-fixed border-collapse text-[13px]">
      <caption className="sr-only">Done per pod</caption>
      <thead>
        <tr className="text-left text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
          <th scope="col" className="w-[38%] pb-1.5 font-bold">
            Pod
          </th>
          <th scope="col" className="pb-1.5 font-bold">
            Done
          </th>
          <th scope="col" className="w-[30%] pb-1.5 text-right font-bold max-sm:w-[34%]">
            Date
          </th>
        </tr>
      </thead>
      <tbody>
        {pods.map((pod) => {
          const share = pod.total > 0 ? pod.done / pod.total : 0;
          return (
            <tr key={pod.id} className="border-t border-grey-border">
              <td className="truncate py-2 pr-2 font-bold text-ink" title={pod.name}>
                {pod.name}
              </td>
              <td className="py-2 pr-2">
                <span className="flex items-center gap-2">
                  <span
                    role="img"
                    aria-label={`${pod.done} of ${pod.total} done`}
                    className="relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-grey-fill"
                  >
                    <span
                      className="absolute inset-y-0 left-0 rounded-full bg-rag-green"
                      style={{ width: `${(share * 100).toFixed(1)}%` }}
                    />
                  </span>
                  <span className="flex-none tabular-nums text-grey-body" aria-hidden>
                    {pod.done}/{pod.total}
                  </span>
                </span>
              </td>
              <td className="py-2 text-right text-grey-secondary">{podDateWords(pod)}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
