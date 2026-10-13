import { useCallback, useMemo, useState } from "react";

import { useMemberId, useOwnTree, usePrograms, useProjects } from "../../app/directory";
import { useRole } from "../../app/role";
import { rankPrograms } from "../../app/scope";
import { ownProjects } from "../delivery/ownTree";
import {
  readRecent,
  recentKey,
  remembered,
  writeRecent,
  type ProjectOption,
} from "./projectChoice";

const browser = () => window.localStorage;

/**
 * The projects the person may open, as the server decides it (R1): a
 * developer's and a scrum master's are their own part of the tree (no "All"
 * group), everyone who reads project progress (product owner, manager,
 * executive, admin) every project, with theirs marked. Also the programmes,
 * worst first, and the browser's recent list for this person.
 */
export function useProjectOptions() {
  const { canReadProjectProgress } = useRole();
  const memberId = useMemberId();
  const projects = useProjects();
  const programs = usePrograms();
  const tree = useOwnTree();
  const key = recentKey(memberId);
  const [stored, setStored] = useState(() => ({ key, recent: readRecent(browser, key) }));
  const recent = stored.key === key ? stored.recent : readRecent(browser, key);

  const options = useMemo<ProjectOption[]>(() => {
    const own = tree.data ? ownProjects(tree.data) : [];
    const mine = new Set(own.map((project) => project.id));
    if (!canReadProjectProgress) {
      const byId = new Map((projects.data ?? []).map((item) => [item.id, item]));
      return own.map((project) => ({
        id: project.id,
        name: project.name,
        rag: project.rag,
        programIds: project.programIds,
        jiraKey: jiraKey(byId.get(project.id)?.metadata),
        mine: true,
      }));
    }
    return (projects.data ?? []).map((project) => ({
      id: project.id,
      name: project.name,
      rag: project.rag ?? "unknown",
      programIds: project.program_ids,
      jiraKey: jiraKey(project.metadata),
      mine: mine.has(project.id),
    }));
  }, [canReadProjectProgress, projects.data, tree.data]);

  const ranked = useMemo(
    () => rankPrograms(programs.data ?? []).map(({ id, name }) => ({ id, name })),
    [programs.data],
  );

  const opened = useCallback(
    (id: string) => {
      const next = remembered(recent, id);
      if (next.join() === recent.join()) return;
      setStored({ key, recent: next });
      writeRecent(browser, key, next);
    },
    [key, recent],
  );

  return {
    options,
    programs: ranked,
    recent,
    /** Keep a project at the top of Recent, in this browser only. */
    opened,
    loading:
      projects.isLoading ||
      programs.isLoading ||
      tree.isLoading ||
      (!canReadProjectProgress && !tree.data && !tree.error),
    error: canReadProjectProgress ? projects.error : (tree.error ?? projects.error),
  };
}

function jiraKey(metadata: Record<string, unknown> | undefined): string | null {
  const value = metadata?.jira_project_key;
  return typeof value === "string" && value.trim() ? value.trim() : null;
}
