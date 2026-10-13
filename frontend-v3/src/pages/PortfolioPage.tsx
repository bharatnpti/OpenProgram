import { Link } from "react-router-dom";

import { useProgramChoice, useProjects } from "../app/directory";
import { projectPath } from "../app/links";
import { useRole } from "../app/role";
import { scopeToProgram } from "../app/scope";
import { PanelState, SectionHeader } from "../components/PanelState";
import { ChipPicker, Panel } from "../components/ui/Bits";
import { ProjectCompactStrip } from "../features/delivery/DeliveryStrips";
import { ProgramPanel } from "../features/delivery/ProgramPanel";
import { ragSeverity } from "../lib/status";

/**
 * Will we make the date? (`/portfolio`): every project's dates, worst first, a
 * programme at a time (`?program=`). For now Delivery's program panel as it
 * was for its readers, and each project's date strip for a product owner, who
 * reads every project's dates but no program roll-up. Lane P builds the page
 * from the kept forecast days.
 */
export function PortfolioPage() {
  const { canReadPortfolio } = useRole();
  const { query, programs, program, choose } = useProgramChoice();
  const projects = useProjects();
  const shown = scopeToProgram(program?.id ?? null, programs.length, {
    projects: projects.data ?? [],
    workstreams: [],
    pods: [],
  }).projects.sort(
    (a, b) => ragSeverity(b.rag) - ragSeverity(a.rag) || a.name.localeCompare(b.name),
  );
  return (
    <>
      <SectionHeader
        title="Will we make the date?"
        meta="Across the portfolio: each project's dates and why, worst first."
      />
      <PanelState
        isLoading={query.isLoading || projects.isLoading}
        error={query.error ?? projects.error}
        isEmpty={shown.length === 0 && programs.length === 0}
        emptyText="No programs are configured yet. An admin adds them under Admin → Entities."
      >
        {programs.length > 1 ? (
          <ChipPicker
            label="Program"
            value={program?.id ?? ""}
            onChange={choose}
            options={programs.map((item) => ({
              value: item.id,
              label: item.name,
              rag: canReadPortfolio ? item.rag : undefined,
            }))}
          />
        ) : null}
        {canReadPortfolio && program ? (
          <ProgramPanel program={program} />
        ) : (
          <Panel title="Projects" note="worst first">
            <ul className="grid gap-4">
              {shown.map((project) => (
                <li key={project.id} className="min-w-0">
                  <Link to={projectPath(project.id)} className="text-[15px] font-extrabold">
                    {project.name}
                  </Link>
                  <ProjectCompactStrip projectId={project.id} />
                </li>
              ))}
            </ul>
          </Panel>
        )}
      </PanelState>
    </>
  );
}
