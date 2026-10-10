// O5, "Acceptance gates" as a heatmap: one row per requirement, one cell per gate,
// each cell its state in a word and a colour. The rows where nothing has started
// fold away under "Show all". Type imports and .ts paths only, so `node --test`
// runs it as written.
import type { GateTemplateDto, IssueGatesResponse } from "../../api/schema";

import { STAGE_ORDER } from "../../components/viz/stages.ts";

/** A cell's state: the gate's own, with "bypassed" for a requirement that moved past it without passing. */
export type HeatState = "passed" | "failed" | "bypassed" | "open" | "incomplete" | "pending" | "na";

export type HeatCell = { state: HeatState; label: string; note: string | null };

const LABELS: Record<HeatState, string> = {
  passed: "Passed",
  failed: "Failed",
  bypassed: "Bypassed",
  open: "Open",
  incomplete: "Incomplete",
  pending: "Not started",
  na: "Does not apply",
};

/**
 * One requirement against one gate. `passed_without` carries gate names, as the
 * server words them: a gate named there and not passed was gone around. A failed
 * item is worse than going around, so it wins. Under the word, what is kept and
 * what Jira suggests: "1 of 2 met · 2 from Jira".
 */
export function heatCell(
  issue: Pick<IssueGatesResponse, "evaluations" | "passed_without">,
  template: Pick<GateTemplateDto, "template_id" | "name">,
): HeatCell {
  const evaluation = issue.evaluations.find((e) => e.template_id === template.template_id);
  if (!evaluation) return { state: "na", label: LABELS.na, note: null };
  const bypassed = issue.passed_without.includes(template.name) && evaluation.state !== "passed";
  const state: HeatState =
    evaluation.state === "failed"
      ? "failed"
      : bypassed
        ? "bypassed"
        : evaluation.state === "passed"
          ? "passed"
          : evaluation.state === "open"
            ? "open"
            : evaluation.total > 0
              ? "incomplete"
              : "pending";
  const parts: string[] = [];
  if (evaluation.total > 0 && state !== "passed") {
    parts.push(`${evaluation.met} of ${evaluation.total} met`);
  }
  if (evaluation.suggested > 0) parts.push(`${evaluation.suggested} from Jira`);
  return { state, label: LABELS[state], note: parts.length > 0 ? parts.join(" · ") : null };
}

export type HeatRow = { issue: IssueGatesResponse; cells: HeatCell[] };

/**
 * Furthest stage first, then by key in number order (CHK-3 before CHK-12), and
 * split: the rows that need someone (a gate started, gone around, failed, or with
 * suggestions to keep) are shown; the rest, where nothing has started, fold.
 */
export function heatRows(
  issues: IssueGatesResponse[],
  templates: GateTemplateDto[],
): { shown: HeatRow[]; folded: HeatRow[] } {
  const rows = [...issues]
    .sort(
      (a, b) =>
        STAGE_ORDER.indexOf(b.stage) - STAGE_ORDER.indexOf(a.stage) ||
        a.key.localeCompare(b.key, undefined, { numeric: true }),
    )
    .map((issue) => ({ issue, cells: templates.map((template) => heatCell(issue, template)) }));
  const quiet = (row: HeatRow) =>
    row.cells.every((cell) => cell.state === "pending" || cell.state === "na") &&
    !row.issue.items.some((item) => item.status === "suggested");
  return { shown: rows.filter((row) => !quiet(row)), folded: rows.filter(quiet) };
}

/** How many cells failed, for the legend's "Failed (none yet)". */
export function failedCells(rows: HeatRow[]): number {
  return rows.reduce((sum, row) => sum + row.cells.filter((c) => c.state === "failed").length, 0);
}
