// No runtime imports, so `node --test` can run it as written.

/** What the browser tab says when no screen has named itself yet, and after every title. */
export const APP_TITLE = "OpenProgram";

const SECTIONS: Record<string, string> = {
  today: "Today",
  delivery: "Delivery",
  signals: "Signals",
  coordination: "Coordination",
  reports: "Reports",
  chat: "Chat",
  admin: "Admin",
  "logged-out": "Signed out",
};

/** The name of a graph node (program, project, workstream or pod) when it is known. */
export type NodeName = (kind: string, id: string) => string | undefined;

/**
 * The browser tab's title for a screen: the thing the screen is about, then its
 * section, then the app, so a row of open tabs tells them apart.
 * "Payments Pod · Delivery · OpenProgram", "Overall report · Checkout Revamp ·
 * OpenProgram", "Signals · OpenProgram". A node nobody has named yet (the
 * directory is still loading) leaves its name out; the id is never shown.
 */
export function screenTitle(pathname: string, nameOf: NodeName = () => undefined): string {
  const [section, first, second] = pathname.split("/").filter(Boolean);
  const label = SECTIONS[section ?? ""];
  if (!label) return APP_TITLE;

  const parts: string[] = [];
  if (section === "delivery" && first && second) {
    const name = nameOf(first, decode(second));
    if (name) parts.push(name);
  } else if (section === "reports" && first) {
    const kind = second === "overall" ? "Overall report" : "Daily report";
    const name = nameOf("project", decode(first));
    parts.push(name ? `${kind} · ${name}` : kind);
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
