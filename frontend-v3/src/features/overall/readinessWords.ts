// Release readiness in words: the answer line, each row's state and urgency, the
// footer, and what a person types before it is sent. Pure, with runtime imports by
// their .ts path, so `node --test` runs it as written.
import type {
  ReadinessBoardResponse,
  ReadinessFindingResponse,
  ReadinessShownState,
} from "../../api/schema";
import { STAGE_LABELS } from "../../components/viz/stages.ts";
import type { BadgeTone } from "../../lib/status.ts";

type Finding = Pick<
  ReadinessFindingResponse,
  | "state"
  | "done"
  | "urgency"
  | "criterion"
  | "candidates"
  | "person_decision"
  | "held"
  | "scope"
  | "evidence"
>;

export const STATE_WORDS: Record<ReadinessShownState, string> = {
  covered: "Covered",
  missing: "Missing",
  unsure: "Unsure",
  not_applicable: "Not applicable",
};

/** Missing is bad news, unsure needs a look, covered and done is good; nothing else is coloured. */
export function stateTone(finding: Pick<Finding, "state" | "done">): BadgeTone {
  if (finding.state === "missing") return "danger";
  if (finding.state === "unsure") return "warning";
  if (finding.state === "covered") return finding.done ? "success" : "info";
  return "neutral";
}

/** "Covered · in progress", "Missing · due 21 Oct (8 working days)", "Unsure · title words only". */
export function stateLine(finding: Finding): string {
  const state = STATE_WORDS[finding.state];
  if (finding.state === "covered") {
    return `${state} · ${finding.done || !finding.criterion.needs_done ? "done" : "in progress"}`;
  }
  if (finding.state === "not_applicable") return state;
  if (finding.state === "unsure" && finding.urgency.kind === "later") {
    return `${state} · ${candidateWords(finding)}`;
  }
  return `${state} · ${urgencyWords(finding)}`;
}

function candidateWords(finding: Pick<Finding, "candidates">): string {
  const kinds = new Set(finding.candidates.map((item) => item.why));
  if (kinds.size === 1 && kinds.has("title_words")) return "title words only";
  if (kinds.size === 1 && kinds.has("epic")) return "its epic only";
  return "a possible match only";
}

/** How soon it is needed: "due 21 Oct (8 working days)", "was due 9 Oct", "CHK-2 reached production". */
export function urgencyWords(finding: Pick<Finding, "urgency" | "criterion">): string {
  const { kind, due_on, working_days_left, stage_key } = finding.urgency;
  const stage = STAGE_LABELS[finding.criterion.required_before].toLowerCase();
  if (kind === "stage_reached") {
    return stage_key ? `${stage_key} reached ${stage}` : `already in ${stage}`;
  }
  if (kind === "no_date") return "no date";
  if (!due_on) return kind === "due_soon" ? `a requirement is about to reach ${stage}` : "no date";
  if (kind === "overdue") return `was due ${shortDay(due_on)}`;
  if (working_days_left === 0) return "due today";
  if (working_days_left !== null && working_days_left > 0 && kind === "due_soon") {
    return `due ${shortDay(due_on)} (${workingDays(working_days_left)})`;
  }
  return `due ${shortDay(due_on)}`;
}

/**
 * The section's answer: "Checkout Revamp is missing 1 of 3 criteria it needs before
 * production; 1 more is unsure." Not applicable ones are not counted.
 */
export function answerLine(
  board: Pick<ReadinessBoardResponse, "findings">,
  subject: string,
): string {
  const counted = board.findings.filter((item) => item.state !== "not_applicable");
  if (counted.length === 0) {
    return `${subject} has no release criteria to meet: every one is marked not applicable.`;
  }
  const missing = counted.filter((item) => item.state === "missing").length;
  const unsure = counted.filter((item) => item.state === "unsure").length;
  const stages = new Set(counted.map((item) => item.criterion.required_before));
  const before =
    stages.size === 1
      ? ` before ${STAGE_LABELS[[...stages][0]].toLowerCase()}`
      : " before it moves on";
  const total = counted.length;
  const noun = total === 1 ? "criterion" : "criteria";
  if (missing > 0) {
    const more = unsure > 0 ? `; ${unsure} more ${unsure === 1 ? "is" : "are"} unsure.` : ".";
    return `${subject} is missing ${missing} of ${total} ${noun} it needs${before}${more}`;
  }
  if (unsure > 0) {
    return `${subject} has evidence for ${total - unsure} of ${total} ${noun} it needs${before}; ${unsure} ${unsure === 1 ? "is" : "are"} unsure.`;
  }
  return `${subject} has evidence in Jira for ${total === 1 ? "the one criterion" : `all ${total} criteria`} it needs${before}.`;
}

/**
 * The most urgent blocking gap, for the section when the answer card above does not
 * already say it: "Security review is due in 8 working days."
 */
export function urgentLine(board: Pick<ReadinessBoardResponse, "findings">): string | null {
  const urgent = board.findings.find(
    (item) =>
      item.criterion.severity === "blocking" &&
      (item.state === "missing" || item.state === "unsure") &&
      !item.held &&
      ["due_soon", "overdue", "stage_reached"].includes(item.urgency.kind),
  );
  if (!urgent) return null;
  const { kind, due_on, working_days_left, stage_key } = urgent.urgency;
  const name = urgent.criterion.name;
  const where = urgent.scope.kind === "pod" ? ` for ${urgent.scope.name}` : "";
  if (kind === "stage_reached") {
    const stage = STAGE_LABELS[urgent.criterion.required_before].toLowerCase();
    return `${stage_key ?? "A requirement"} reached ${stage} without it: ${name}${where}.`;
  }
  if (kind === "overdue" && due_on) return `${name}${where} was due ${shortDay(due_on)}.`;
  if (working_days_left !== null && working_days_left > 0) {
    return `${name}${where} is due in ${workingDays(working_days_left)}.`;
  }
  return `${name}${where} is due today.`;
}

/** "Release 1: 1 missing, 1 unsure", for a release's line in the whole-project view. */
export function releaseLineWords(line: {
  name: string;
  missing: number;
  unsure: number;
  total: number;
}): string {
  if (line.total === 0) return `${line.name}: not checked yet`;
  const parts = [
    line.missing ? `${line.missing} missing` : null,
    line.unsure ? `${line.unsure} unsure` : null,
  ].filter((part): part is string => part !== null);
  return `${line.name}: ${parts.length ? parts.join(", ") : `all ${line.total} covered`}`;
}

/** "Checked 07:45 · Jira data from 07:00", or what a failing sync means for the rows. */
export function footerWords(
  agent: ReadinessBoardResponse["agent"],
  time: (iso: string) => string,
): string {
  if (agent.stale) {
    const since = agent.data_as_of ? `; this shows Jira as of ${time(agent.data_as_of)}` : "";
    return `The Jira sync is failing or behind${since}. A new gap waits for fresh data before the day report says it.`;
  }
  const parts = [
    agent.last_run_at ? `Checked ${time(agent.last_run_at)}` : "Not checked yet",
    agent.data_as_of ? `Jira data from ${time(agent.data_as_of)}` : null,
  ].filter((part): part is string => part !== null);
  return parts.join(" · ");
}

/** "Linked by Mina Patel, 2 Oct", "Not applicable: internal API only (Asha Rao, 2 Oct)". */
export function decisionWords(
  decision: ReadinessFindingResponse["person_decision"],
): string | null {
  if (!decision) return null;
  const who = decision.by_name ?? decision.by;
  const when = shortDay(decision.at);
  if (decision.kind === "not_applicable") {
    return `Not applicable: ${decision.reason} (${who}, ${when})`;
  }
  if (decision.created) return `Created from this draft by ${who}, ${when}`;
  return `Linked by ${who}, ${when}`;
}

/** The rows under their headings: the release's and project's first, then each pod's. */
export function rowGroups<T extends Pick<Finding, "scope">>(
  findings: T[],
): { heading: string | null; rows: T[] }[] {
  const groups: { heading: string | null; rows: T[] }[] = [];
  for (const finding of findings) {
    const heading = finding.scope.kind === "pod" ? finding.scope.name : null;
    const group = groups.find((item) => item.heading === heading);
    if (group) group.rows.push(finding);
    else groups.push({ heading, rows: [finding] });
  }
  return groups.sort((a, b) => Number(a.heading !== null) - Number(b.heading !== null));
}

/** What a person typed to link, read as a Jira key or a record's https:// link. */
export function linkTarget(
  text: string,
  note = "",
):
  | { issue_key: string; note: string }
  | { evidence_url: string; note: string }
  | { problem: string } {
  const clean = text.trim();
  if (!clean)
    return { problem: "Type a Jira key, such as CHK-12, or a link starting with https://." };
  if (/^https:\/\/\S+$/i.test(clean)) return { evidence_url: clean, note: note.trim() };
  if (/^[a-z][a-z0-9_]*-\d+$/i.test(clean)) return { issue_key: clean.toUpperCase(), note: "" };
  // An issue the server knows by another key (a sample tenant's ids): it matches it as typed.
  if (/^[a-z][a-z0-9_-]*-\d+$/i.test(clean)) return { issue_key: clean, note: "" };
  if (/^https?:/i.test(clean)) return { problem: "A record's link starts with https://." };
  return { problem: "A Jira key looks like CHK-12." };
}

/** A reason is 3 to 300 characters, as the server checks. */
export function reasonProblem(text: string): string | null {
  const clean = text.trim().replace(/\s+/g, " ");
  if (clean.length < 3 || clean.length > 300) return "Give a reason of 3 to 300 characters.";
  return null;
}

/** The few words a draft's facts take on one line: "CHK · Task · security-review · unassigned". */
export function draftFacts(draft: {
  project_key: string;
  issue_type: string;
  labels?: string[];
}): string {
  const labels = draft.labels ?? [];
  return [draft.project_key || "no project", draft.issue_type, ...labels, "unassigned"].join(" · ");
}

/** One audit row in words, for a finding's "What happened". */
export const ACTION_WORDS: Record<string, string> = {
  state_changed: "The check updated it",
  drafted: "Drafted a Jira issue",
  draft_rerendered: "Redrafted it after the criterion changed",
  draft_edited: "Edited the draft",
  dismissed: "Dismissed the draft",
  linked: "Linked evidence",
  not_applicable: "Marked it not applicable",
  reopened: "Reopened it",
  create_requested: "Asked Jira to create it",
  created: "Created it in Jira",
  create_adopted: "Found the issue an earlier try made",
  create_failed: "Jira did not create it",
};

/** "Asha Rao", or "The readiness check" for the agent's own changes. */
export function actorWords(actor: string, name: string | null | undefined): string {
  if (actor === "agent") return "The readiness check";
  return name ?? actor;
}

function workingDays(days: number): string {
  return days === 1 ? "1 working day" : `${days} working days`;
}

/** "21 Oct" for an ISO day or timestamp. */
export function shortDay(iso: string): string {
  const day = new Date(`${iso.slice(0, 10)}T12:00:00`);
  return day.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
}
