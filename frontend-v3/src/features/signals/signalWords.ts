// Pure wording and grouping for Signals › Risks. Type imports, and pure modules
// imported by their .ts path, so `node --test` runs it as written.
import type { DriftFindingResponse, Rag, RiskFindingResponse } from "../../api/schema";
import { ownerSourceWords, statedSourceWords } from "../../lib/checkinWords.ts";
import { ragSeverity } from "../../lib/status.ts";

/** A risk or a drift finding, as one row of the list. */
export type Finding =
  | { type: "risk"; key: string; severity: Rag; finding: RiskFindingResponse }
  | { type: "drift"; key: string; severity: Rag; finding: DriftFindingResponse };

type Findings = { risks: RiskFindingResponse[]; drift: DriftFindingResponse[] };

/**
 * What makes a finding the same finding in two reads (a project's and the
 * portfolio's). A person can have several merge requests open, so the evidence
 * makes a risk its own.
 */
export function findingKey(item: Finding): string {
  const f = item.finding;
  return item.type === "risk"
    ? `risk|${item.finding.rule_id}|${f.entity_ref.kind}:${f.entity_ref.id}|${f.evidence?.identifier ?? ""}`
    : `drift|${item.finding.kind}|${f.entity_ref.kind}:${f.entity_ref.id}|${f.evidence?.identifier ?? ""}`;
}

/** A read's risks and drift as rows, worst first, then oldest first. */
export function findingsOf(read: Findings): Finding[] {
  const rows: Finding[] = [
    ...read.risks.map((finding) => ({
      type: "risk" as const,
      key: "",
      severity: finding.severity,
      finding,
    })),
    ...read.drift.map((finding) => ({
      type: "drift" as const,
      key: "",
      severity: finding.severity,
      finding,
    })),
  ].map((row) => ({ ...row, key: findingKey(row) }));
  return rows.sort(
    (a, b) => ragSeverity(b.severity) - ragSeverity(a.severity) || ageDays(b) - ageDays(a),
  );
}

function ageDays(item: Finding): number {
  return item.type === "risk" ? item.finding.age_days : 0;
}

export type RiskGroup = {
  /** Null for the findings no project's read holds. */
  projectId: string | null;
  name: string;
  worst: Rag;
  findings: Finding[];
};

/**
 * One list across projects, grouped by project, the worst project first (by
 * its worst finding, then how many it has, then by name), and in each the
 * worst finding first. A finding that only the portfolio read holds (one filed
 * on no project) comes last, under its own heading, so nothing is dropped. A
 * finding two projects share is listed under each.
 */
export function groupByProject(
  projects: { id: string; name: string }[],
  perProject: (projectId: string) => Findings | undefined,
  portfolio: Findings | undefined,
): RiskGroup[] {
  const seen = new Set<string>();
  const groups: RiskGroup[] = [];
  for (const project of projects) {
    const read = perProject(project.id);
    if (!read) continue;
    const findings = findingsOf(read);
    if (findings.length === 0) continue;
    findings.forEach((item) => seen.add(item.key));
    groups.push({ projectId: project.id, name: project.name, worst: worstOf(findings), findings });
  }
  groups.sort(
    (a, b) =>
      ragSeverity(b.worst) - ragSeverity(a.worst) ||
      b.findings.length - a.findings.length ||
      a.name.localeCompare(b.name),
  );
  const rest = portfolio ? findingsOf(portfolio).filter((item) => !seen.has(item.key)) : [];
  if (rest.length > 0) {
    groups.push({
      projectId: null,
      name: "Not tied to a project",
      worst: worstOf(rest),
      findings: rest,
    });
  }
  return groups;
}

function worstOf(findings: Finding[]): Rag {
  return findings.reduce<Rag>(
    (worst, item) => (ragSeverity(item.severity) > ragSeverity(worst) ? item.severity : worst),
    "green",
  );
}

/** The header's count: "7 open risks", and "· 2 drift" only when there is some. */
export function riskCountLine(risks: number, drift: number): string {
  const open = `${risks} open ${risks === 1 ? "risk" : "risks"}`;
  return drift > 0 ? `${open} · ${drift} drift` : open;
}

/**
 * The one grey line under a finding: what its owner says, and how they said it.
 * "Noah Weber says: CHK-6 MR will be ready for review tomorrow (replied)". `who`
 * is the owner's name, or null when nobody names them.
 */
export function ownerLine(item: Finding, who: string | null): string {
  if (item.type === "drift") {
    const owner = item.finding.owner_id ? (who ?? "The owner") : "Nobody is named as the owner";
    return `${owner}: ${statedSourceWords(item.finding.stated_source)}`;
  }
  const risk = item.finding;
  if (!risk.person_name && !risk.owner_id) {
    return "Nobody owns this work item, so nobody has said anything about it.";
  }
  const name = who ?? "The owner";
  const said = risk.owner_status_summary?.trim();
  const how = ownerSourceWords(risk.owner_status_source);
  return said ? `${name} says: ${said} (${how})` : `${name} has reported nothing (${how})`;
}

/** How old a finding is: a risk's days open, a drift finding's first day. */
export function ageWords(item: Finding, formatDay: (iso: string) => string): string {
  if (item.type === "drift") return `since ${formatDay(item.finding.detected_at)}`;
  const days = item.finding.age_days;
  return days <= 0 ? "today" : `${days}d`;
}
