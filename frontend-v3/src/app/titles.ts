// Only pure modules imported, by their .ts path, so `node --test` can run it as written.
import { ASK_LABEL } from "../features/assistant/persona.ts";
import { ALL_PROJECTS, HUB_TAB_LABELS, parseHub } from "../features/hub/hubPaths.ts";

/** What the browser tab says when no screen has named itself yet, and after every title. */
export const APP_TITLE = "OpenProgram";

const SECTIONS: Record<string, string> = {
  today: "Today",
  "check-in": "Check-in",
  requests: "Requests",
  projects: "Projects",
  flow: "Review flow",
  ora: ASK_LABEL,
  people: "People & roles",
  admin: "Admin",
  "logged-out": "Signed out",
};

/** The name of a graph node (program, project, workstream or pod) when it is known. */
export type NodeName = (kind: string, id: string) => string | undefined;

/**
 * The browser tab's title for a screen: the thing the screen is about, then
 * the question it answers, then the app, so a row of open tabs tells them
 * apart. In the Projects hub the thing is the pod, the project or All
 * projects, and the question is the tab: "Checkout Revamp · Early warnings ·
 * OpenProgram", "Payments Pod · Checkout Revamp · Will we make the date? ·
 * OpenProgram", "All projects · Reports · OpenProgram". Elsewhere it is the
 * page: "Review flow · OpenProgram". A node nobody has named yet (the
 * directory is still loading) leaves its name out; the id is never shown.
 */
export function screenTitle(pathname: string, nameOf: NodeName = () => undefined): string {
  const hub = parseHub(pathname);
  if (hub) {
    const { place, tab } = hub;
    const parts: string[] = [];
    if (place.level === "all") parts.push(ALL_PROJECTS);
    if (place.level === "pod") {
      const pod = nameOf("pod", place.podId);
      if (pod) parts.push(pod);
    }
    if (place.level !== "all") {
      const project = nameOf("project", place.projectId);
      if (project) parts.push(project);
    }
    parts.push(tab ? HUB_TAB_LABELS[tab] : SECTIONS.projects);
    return [...parts, APP_TITLE].join(" · ");
  }
  const [section] = pathname.split("/").filter(Boolean);
  if (!section || !Object.prototype.hasOwnProperty.call(SECTIONS, section)) return APP_TITLE;
  return [SECTIONS[section], APP_TITLE].join(" · ");
}
