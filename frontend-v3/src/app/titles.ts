// Only pure modules imported, by their .ts path, so `node --test` can run it as written.
import { ASK_LABEL } from "../features/assistant/persona.ts";

/** What the browser tab says when no screen has named itself yet, and after every title. */
export const APP_TITLE = "OpenProgram";

const SECTIONS: Record<string, string> = {
  today: "Today",
  "check-in": "Check-in",
  requests: "Requests",
  portfolio: "Portfolio",
  projects: "Projects",
  pods: "Pods",
  warnings: "Early warnings",
  scope: "Scope control",
  reports: "Reports",
  flow: "Review flow",
  ora: ASK_LABEL,
  people: "People & roles",
  admin: "Admin",
  "logged-out": "Signed out",
};

/** The name of a graph node (program, project, workstream or pod) when it is known. */
export type NodeName = (kind: string, id: string) => string | undefined;

/**
 * The browser tab's title for a screen: the thing the screen is about, then its
 * section, then the app, so a row of open tabs tells them apart.
 * "Checkout Revamp · Projects · OpenProgram", "Payments Pod · Pods ·
 * OpenProgram", "Daily report · Checkout Revamp · Reports · OpenProgram",
 * "Review flow · OpenProgram". A node nobody has named yet (the directory is
 * still loading) leaves its name out; the id is never shown.
 */
export function screenTitle(pathname: string, nameOf: NodeName = () => undefined): string {
  const [section, first] = pathname.split("/").filter(Boolean);
  if (!section || !Object.prototype.hasOwnProperty.call(SECTIONS, section)) return APP_TITLE;
  const label = SECTIONS[section];

  const parts: string[] = [];
  if ((section === "projects" || section === "pods") && first) {
    const name = nameOf(section === "projects" ? "project" : "pod", decode(first));
    if (name) parts.push(name);
  } else if (section === "reports" && first === "briefs") {
    parts.push("Briefs");
  } else if (section === "reports" && first) {
    const name = nameOf("project", decode(first));
    parts.push(name ? `Daily report · ${name}` : "Daily report");
  }
  return [...parts, label, APP_TITLE].join(" · ");
}

function decode(segment: string): string {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
}
