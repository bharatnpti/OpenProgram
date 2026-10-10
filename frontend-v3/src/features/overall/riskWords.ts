// Risks and drift as one line each: worst first, then oldest, with what the
// owner says folded under it. Type imports and .ts paths only, so `node --test`
// runs it as written.
import type { ProjectRisksResponse, Rag } from "../../api/schema";

import { daysBetween } from "../../lib/format.ts";
import { ragSeverity } from "../../lib/status.ts";

export type RiskLine = {
  key: string;
  severity: Rag;
  what: string;
  /** "acme/checkout-api#3 · risk" */
  meta: string;
  owner: string;
  /** "What Noah says", or null when the owner said nothing to fold. */
  saysTitle: string | null;
  says: string | null;
  age: number;
};

/** "Red", for the words a screen reader hears before the line. */
export const SEVERITY_WORDS: Record<Rag, string> = {
  red: "Red",
  amber: "Amber",
  green: "Green",
  unknown: "Not rated",
};

/**
 * One line per finding. A risk names its owner when the API does, else the
 * directory does (`name`); a person nobody names stays an id, and their
 * statement is then "What the owner says".
 */
export function riskLines(
  data: Pick<ProjectRisksResponse, "risks" | "drift">,
  name: (id: string | null | undefined) => string,
  known: (id: string | null | undefined) => boolean,
  today: string,
): RiskLine[] {
  const says = (owner: string, ownerKnown: boolean) =>
    ownerKnown && owner.trim()
      ? `What ${owner.trim().split(/\s+/)[0]} says`
      : "What the owner says";
  const lines: RiskLine[] = [
    ...data.risks.map((r) => {
      const owner = r.person_name ?? name(r.owner_id);
      return {
        // A person can have several merge requests open: the request makes the finding its own.
        key: `risk-${r.rule_id}-${r.entity_ref.id}-${r.evidence.identifier}`,
        severity: r.severity,
        what: r.reason,
        meta: `${r.evidence.identifier} · risk`,
        owner,
        saysTitle: r.owner_status_summary
          ? says(owner, Boolean(r.person_name) || known(r.owner_id))
          : null,
        says: r.owner_status_summary,
        age: r.age_days,
      };
    }),
    ...data.drift.map((d) => ({
      key: `drift-${d.kind}-${d.entity_ref.id}-${d.evidence?.identifier ?? ""}`,
      severity: d.severity,
      what: d.reason,
      meta: `${d.evidence?.identifier ?? d.entity_ref.id} · drift`,
      owner: name(d.owner_id),
      saysTitle: null,
      says: null,
      age: Math.max(0, daysBetween(d.detected_at, today)),
    })),
  ];
  return lines.sort((a, b) => ragSeverity(b.severity) - ragSeverity(a.severity) || b.age - a.age);
}
