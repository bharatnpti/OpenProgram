// Jira hygiene on Overall, in words: the answer line, each rule's counts, who is
// asked and how they were found, and what a person decided. Findings are about
// issues; nothing here counts or ranks people.
//
// Type imports only, so `node --test` runs it as written.
import type {
  HygieneBoardResponse,
  HygieneFindingResponse,
  HygieneOwnerKind,
  HygieneRuleRowDto,
} from "../../api/schema";

type Finding = HygieneFindingResponse;

/** "5 open findings under 2 rules, 2 of them new today." / "No issue breaks a rule today." */
export function answerLine(board: Pick<HygieneBoardResponse, "rules" | "open">): string {
  const open = board.open;
  if (open === 0) return "No issue breaks a rule today.";
  const breaking = board.rules.filter((row) => row.open > 0).length;
  const fresh = board.rules.reduce((sum, row) => sum + row.new_today, 0);
  // One issue may break two rules: these are findings, each one issue under one rule.
  const findings = open === 1 ? "1 open finding" : `${open} open findings`;
  const rules = breaking === 1 ? "1 rule" : `${breaking} rules`;
  const tail =
    fresh === 0 ? "" : fresh === open ? ", all new today" : `, ${fresh} of them new today`;
  return `${findings} under ${rules}${tail}.`;
}

/** "4 open · 1 new today · oldest 6 days" / "Nothing open". */
export function rowCounts(
  row: Pick<HygieneRuleRowDto, "open" | "new_today" | "oldest_days" | "decided">,
): string {
  const parts: string[] = [];
  if (row.open === 0) parts.push("Nothing open");
  else parts.push(`${row.open} open`);
  if (row.new_today > 0) parts.push(`${row.new_today} new today`);
  if (
    row.open > 0 &&
    row.oldest_days !== null &&
    row.oldest_days !== undefined &&
    row.oldest_days > 0
  ) {
    parts.push(`oldest ${days(row.oldest_days)}`);
  }
  if (row.decided > 0) parts.push(`${row.decided} set aside`);
  return parts.join(" · ");
}

/** What the rule does when it finds something. */
export function modeWords(row: Pick<HygieneRuleRowDto, "severity">): string {
  return row.severity === "ask" ? "Asks the owner" : "Lists only";
}

const VIA: Record<HygieneOwnerKind, string> = {
  assignee: "the assignee",
  reporter: "the reporter",
  profile: "the pod's",
  pod_scrum_master: "the pod's scrum master",
  decision_owner: "who decides",
};

/** "Kai Lund, the assignee" / "Sofia Berg, the pod's QA and test" / "Nobody named yet". */
export function ownerWords(owner: Finding["owner"]): string {
  if (!owner.name) return "Nobody named yet";
  if (!owner.via) return owner.name;
  if (owner.via === "profile") {
    return owner.profile_label ? `${owner.name}, the pod's ${owner.profile_label}` : owner.name;
  }
  return `${owner.name}, ${VIA[owner.via]}`;
}

/** "Waived by Mina Patel: spike work" / "Set aside by Asha Rao: not ours". */
export function decisionWords(finding: Pick<Finding, "state" | "decision">): string | null {
  const decision = finding.decision;
  if (!decision) return null;
  const verb = finding.state === "not_applicable" ? "Set aside" : "Waived";
  const who = decision.by_name ?? "a team member";
  return `${verb} by ${who}: ${decision.reason}`;
}

/** "Open since today" / "Open 3 days". */
export function openWords(finding: Pick<Finding, "open_days">): string {
  return finding.open_days <= 0 ? "Open since today" : `Open ${days(finding.open_days)}`;
}

/** The findings of one rule: open first, by key; then what people decided. */
export function findingsOf(
  findings: readonly Finding[],
  ruleId: string,
): { open: Finding[]; decided: Finding[] } {
  const mine = findings.filter((item) => item.rule_id === ruleId);
  const byKey = (a: Finding, b: Finding) => naturalKey(a.issue_key, b.issue_key);
  return {
    open: mine.filter((item) => item.state === "open").sort(byKey),
    decided: mine.filter((item) => item.state !== "open").sort(byKey),
  };
}

/** "The Jira sync is behind; nothing closes until it catches up." or the last check. */
export function footerWords(
  scan: HygieneBoardResponse["scan"],
  held: number,
  time: (iso: string) => string,
): string {
  if (scan.stale) {
    const since = scan.data_as_of ? `; this shows Jira as of ${time(scan.data_as_of)}` : "";
    return `The Jira sync is failing or behind${since}. Nothing closes until fresh data comes, and a new ask waits for it.`;
  }
  const parts = [
    scan.last_run_at ? `Checked ${time(scan.last_run_at)}` : "Not checked yet",
    scan.data_as_of ? `Jira data from ${time(scan.data_as_of)}` : null,
  ].filter((part): part is string => part !== null);
  const waiting =
    held > 0
      ? ` ${held === 1 ? "1 finding" : `${held} findings`} read while the sync was behind ${held === 1 ? "waits" : "wait"} for fresh data before the day report says ${held === 1 ? "it" : "them"}.`
      : "";
  return parts.join(" · ") + (parts.length ? "." : "") + waiting;
}

export function reasonProblem(text: string): string | null {
  const clean = text.trim().replace(/\s+/g, " ");
  if (clean.length < 3 || clean.length > 300) return "Give a reason of 3 to 300 characters.";
  return null;
}

/** Whether the section is drawn at all: the scan is on, or something is still shown. */
export function hasSection(board: Pick<HygieneBoardResponse, "scan" | "rules">): boolean {
  return board.scan.enabled || board.rules.some((row) => row.open > 0);
}

function days(count: number): string {
  return count === 1 ? "1 day" : `${count} days`;
}

function naturalKey(a: string, b: string): number {
  const [headA, numA] = split(a);
  const [headB, numB] = split(b);
  return headA === headB ? numA - numB : headA.localeCompare(headB);
}

function split(key: string): [string, number] {
  const at = key.lastIndexOf("-");
  const tail = Number(key.slice(at + 1));
  return at > 0 && Number.isFinite(tail) ? [key.slice(0, at), tail] : [key, 0];
}
