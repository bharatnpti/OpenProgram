// The Project page's sections, in one place (perspective-ux §3.3): Overall's
// sections and Delivery's project panel merged, date first, then scope, quality,
// questions and warnings, and the work last. The page draws the sections it has,
// in this order; a section a role cannot use is not drawn. A new section joins
// by its key here and one element in pages/ProjectPage.tsx. No imports, so
// `node --test` runs it.

export type ProjectSection =
  /** The answer banner with the three dates. */
  | "answer"
  /** The timeline, What changed for the picked row, and why its date moved. */
  | "timeline"
  /** One line of the planning baseline and Jira hygiene, linking to Scope control. */
  | "scope"
  /** Requirements by stage. */
  | "requirements"
  /** The gates per requirement. */
  | "gates"
  /** Release readiness, under the gates. */
  | "readiness"
  | "questions"
  /** This project's early warnings, linking to Early warnings. */
  | "warnings"
  /** Pods, Jira and repositories, people, progress and today's health. */
  | "work";

export const PROJECT_ORDER: readonly ProjectSection[] = [
  "answer",
  "timeline",
  "scope",
  "requirements",
  "gates",
  "readiness",
  "questions",
  "warnings",
  "work",
];

/** The in-page index: each anchor and its word, in the page's order. */
export const PROJECT_ANCHORS: readonly { section: ProjectSection; id: string; label: string }[] = [
  { section: "timeline", id: "timeline", label: "Date" },
  { section: "scope", id: "scope", label: "Scope" },
  { section: "requirements", id: "stages", label: "Stages" },
  { section: "gates", id: "gates", label: "Gates" },
  { section: "questions", id: "questions", label: "Questions" },
  { section: "warnings", id: "warnings", label: "Warnings" },
  { section: "work", id: "work", label: "Work" },
];

/** The sections to draw, in the page's order: those the page has an element for. */
export function projectSections<T>(
  elements: Partial<Record<ProjectSection, T | null | false | undefined>>,
): { key: ProjectSection; element: T }[] {
  return PROJECT_ORDER.flatMap((key) => {
    const element = elements[key];
    return element === null || element === false || element === undefined ? [] : [{ key, element }];
  });
}

/** The index's anchors for the sections drawn; none when fewer than three would show. */
export function projectAnchors(drawn: readonly ProjectSection[]): { id: string; label: string }[] {
  const anchors = PROJECT_ANCHORS.filter((anchor) => drawn.includes(anchor.section));
  return anchors.length >= 3 ? anchors.map(({ id, label }) => ({ id, label })) : [];
}
