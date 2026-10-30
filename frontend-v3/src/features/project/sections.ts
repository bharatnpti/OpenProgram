// A project's sections, tab by tab, in one place: Overall's sections and
// Delivery's project panel, each said once, on the tab whose question it
// answers. A tab draws the sections it has, in this order; a section a role
// cannot use is not drawn. A new section joins by its key here and one
// element in its tab's view (pages/hub/Project*.tsx). No imports, so
// `node --test` runs it.

export type ProjectSection =
  /** The answer banner with the three dates. */
  | "answer"
  /** The timeline, What changed for the picked row, and why its date moved. */
  | "timeline"
  /** Requirements by stage. */
  | "requirements"
  /** Pods, Jira and repositories, people, progress and today's health. */
  | "work"
  /** This project's early warnings. */
  | "warnings"
  | "questions"
  /** The planning baseline: what joined after planning, Mark planning complete. */
  | "baseline"
  /** The gates per requirement. */
  | "gates"
  /** Release readiness, under the gates. */
  | "readiness"
  /** Jira hygiene. */
  | "hygiene";

/** Each tab's sections, in the order they are drawn. */
export const TAB_SECTIONS = {
  date: ["answer", "timeline", "requirements", "work"],
  warnings: ["warnings", "questions"],
  scope: ["baseline", "gates", "readiness", "hygiene"],
} as const satisfies Record<string, readonly ProjectSection[]>;

export type SectionTab = keyof typeof TAB_SECTIONS;

/** The in-page index: each anchor and its word, in the page's order. */
export const PROJECT_ANCHORS: readonly { section: ProjectSection; id: string; label: string }[] = [
  { section: "timeline", id: "timeline", label: "Timeline" },
  { section: "requirements", id: "stages", label: "Stages" },
  { section: "work", id: "work", label: "Work" },
  { section: "warnings", id: "warnings", label: "Warnings" },
  { section: "questions", id: "questions", label: "Questions" },
  { section: "baseline", id: "baseline", label: "Against the plan" },
  { section: "gates", id: "gates", label: "Gates" },
  { section: "readiness", id: "readiness", label: "Readiness" },
  { section: "hygiene", id: "hygiene", label: "Jira hygiene" },
];

/** A tab's sections to draw, in its order: those the tab has an element for. */
export function projectSections<T>(
  tab: SectionTab,
  elements: Partial<Record<ProjectSection, T | null | false | undefined>>,
): { key: ProjectSection; element: T }[] {
  return TAB_SECTIONS[tab].flatMap((key) => {
    const element = elements[key];
    return element === null || element === false || element === undefined ? [] : [{ key, element }];
  });
}

/** The index's anchors for the sections drawn; none when fewer than three would show. */
export function projectAnchors(drawn: readonly ProjectSection[]): { id: string; label: string }[] {
  const anchors = PROJECT_ANCHORS.filter((anchor) => drawn.includes(anchor.section));
  return anchors.length >= 3 ? anchors.map(({ id, label }) => ({ id, label })) : [];
}
