// Scope control (`/scope`): the table across projects in words, pure, from the bulk
// read (`GET /portfolio/scope`, D90). Keys and counts, never who added anything.
// Type imports, and runtime imports by their .ts path, so `node --test` runs it.
import type { ScopeFindingDto, ScopeProjectDto } from "../../api/schema";
import { formatDay } from "../../lib/format.ts";

/** "2 (67%)": the added requirements without the baseline's own change label. */
export function withoutLabelWords(project: Pick<ScopeProjectDto, "plan">): string {
  const plan = project.plan;
  if (!plan) return "—";
  if (plan.added === 0) return "0";
  return `${plan.without_label} (${plan.without_label_percent ?? 0}%)`;
}

/** The column's heading names the tenant's label when the projects share one. */
export function labelHeading(projects: readonly ScopeProjectDto[]): string {
  const labels = new Set(
    projects.flatMap((project) => (project.plan ? [project.plan.change_label] : [])),
  );
  return labels.size === 1 ? `Without the ${[...labels][0]} label` : "Without the change label";
}

/** "+3 since 26 May": what joined after planning, for a row's reader. */
export function addedWords(project: Pick<ScopeProjectDto, "plan">): string {
  const plan = project.plan;
  if (!plan) return "No baseline";
  return `+${plan.added}`;
}

/** Who owns a finding, as this reader may see it: by name, else the team, never an id. */
export function ownerWords(finding: Pick<ScopeFindingDto, "owner_name" | "owner_team">): string {
  if (finding.owner_name) {
    return finding.owner_team
      ? `${finding.owner_name} · ${finding.owner_team}`
      : finding.owner_name;
  }
  return finding.owner_team ?? "Nobody named yet";
}

/** "Found Tue 12 Jun · 3 days": when the scan first saw it, and how long it is open. */
export function foundWords(finding: Pick<ScopeFindingDto, "found_on" | "open_days">): string {
  const days = finding.open_days;
  const open = days === 0 ? "today" : `${days} ${days === 1 ? "day" : "days"}`;
  return `${formatDay(finding.found_on)} · ${open}`;
}

/** The planning line under a project: "Planned 8 on Sat 5 Oct · 1 removed". */
export function planningWords(project: Pick<ScopeProjectDto, "plan" | "scope_day">): string | null {
  const plan = project.plan;
  if (!plan) return null;
  const parts = [`${plan.planned} planned on ${formatDay(plan.planned_on)}`];
  if (plan.removed) parts.push(`${plan.removed} removed`);
  if (project.scope_day) parts.push(`scope as of ${formatDay(project.scope_day)}`);
  return parts.join(" · ");
}
