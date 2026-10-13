import { useEffect } from "react";
import { Navigate, useLocation, useParams } from "react-router-dom";

import { projectPath } from "../app/links";
import { useDayWords } from "../app/viewingDate";
import { PanelState } from "../components/PanelState";
import { useAssistantSubject } from "../features/assistant/assistantContext";
import { defaultProject } from "../features/project/projectChoice";
import { useProjectOptions } from "../features/project/useProjectOptions";
import { OverallBody } from "./OverallPage";

/**
 * One project, at `/projects/:projectId` (D86): one page for every project, the
 * URL its address, never a nav entry per project. A bare `/projects` opens the
 * worst of the person's own (else the last opened, else the worst), keeping the
 * link's query and hash. The URL always wins.
 */
export function ProjectPage() {
  const { projectId = "" } = useParams();
  const location = useLocation();
  const day = useDayWords();
  const choice = useProjectOptions();
  const { opened } = choice;
  const project = choice.options.find((option) => option.id === projectId);
  useAssistantSubject(project ? { kind: "project", name: project.name } : null);
  useEffect(() => {
    if (project) opened(project.id);
  }, [opened, project]);

  if (!projectId) {
    const id = choice.loading ? null : defaultProject(choice.options, choice.recent);
    if (id) {
      return (
        <Navigate
          to={{ pathname: projectPath(id), search: location.search, hash: location.hash }}
          replace
        />
      );
    }
    return (
      <PanelState
        isLoading={choice.loading}
        error={choice.error}
        isEmpty
        emptyText={
          day === "today"
            ? "No project is yours to open yet."
            : `No project was yours to open ${day}.`
        }
      >
        {null}
      </PanelState>
    );
  }

  return (
    <>
      <div className="mb-5">
        <p className="text-[12px] font-bold uppercase tracking-wider text-grey-secondary">
          Project
        </p>
        <h1 className="mt-1 text-[30px] font-extrabold tracking-tight text-balance">
          {project?.name ?? "Project"}
        </h1>
      </div>
      <OverallBody projectId={projectId} />
    </>
  );
}
