import { useEffect } from "react";
import { useLocation } from "react-router-dom";

import { usePods, usePrograms, useProjects, useWorkstreams } from "../../app/directory";
import { APP_TITLE, screenTitle, type NodeName } from "../../app/titles";

/**
 * Gives the browser tab the title of the screen shown, so a row of open tabs
 * (Today, a pod, a project's report) can be told apart. Renders nothing. The
 * names come from the directory every role reads, already cached by the
 * screens themselves.
 */
export function TabTitle() {
  const { pathname } = useLocation();
  const programs = usePrograms().data;
  const projects = useProjects().data;
  const workstreams = useWorkstreams().data;
  const pods = usePods().data;

  useEffect(() => {
    const lists: Record<string, { id: string; name: string }[] | undefined> = {
      program: programs,
      project: projects,
      workstream: workstreams,
      pod: pods,
    };
    const nameOf: NodeName = (kind, id) => lists[kind]?.find((node) => node.id === id)?.name;
    document.title = screenTitle(pathname, nameOf);
    return () => {
      document.title = APP_TITLE;
    };
  }, [pathname, programs, projects, workstreams, pods]);

  return null;
}
