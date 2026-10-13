// The order of Overall's sections, in one place (DESIGN §5.3): date first, then
// scope, quality and people. The page draws the sections it has, in this order;
// a section a role cannot use is not drawn. A new section joins by its key here
// and one element in OverallPage. No imports, so `node --test` runs it.

export type OverallSection =
  /** The answer line with its date strip. */
  | "answer"
  /** The Gantt per project, release and pod, then why the picked row's date moved. */
  | "timeline"
  /** The planning baseline the timeline's rows start from. */
  | "baseline"
  | "requirements"
  | "gates"
  | "readiness"
  /** Jira hygiene: each rule's open findings, for the readiness board's readers. */
  | "hygiene"
  | "risks"
  | "questions";

export const OVERALL_ORDER: readonly OverallSection[] = [
  "answer",
  "timeline",
  "baseline",
  "requirements",
  "gates",
  "readiness",
  "hygiene",
  "risks",
  "questions",
];

/** The sections to draw, in Overall's order: those the page has an element for. */
export function overallSections<T>(
  elements: Partial<Record<OverallSection, T | null | false | undefined>>,
): { key: OverallSection; element: T }[] {
  return OVERALL_ORDER.flatMap((key) => {
    const element = elements[key];
    return element === null || element === false || element === undefined ? [] : [{ key, element }];
  });
}
