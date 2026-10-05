// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  ContactSource,
  EscalationLevelDto,
  EscalationMatrixRequest,
  EscalationMatrixResponse,
  MatrixSource,
  NeedType,
} from "../../api/schema";

export const NEEDS: NeedType[] = ["fix", "decision", "answer", "review"];

export const NEED_LABELS: Record<NeedType, string> = {
  fix: "Fix",
  decision: "Decision",
  answer: "Answer",
  review: "Review",
};

export const NEED_HINTS: Record<NeedType, string> = {
  fix: "A blocker, a failed check, someone waiting on the work",
  decision: "Accepting a requirement, signing its criteria off",
  answer: "A question asked on an issue, a request for input",
  review: "A code review, suggestions read from Jira",
};

export const SOURCES: ContactSource[] = ["team_scrum_master", "team_manager", "member"];

export const SOURCE_LABELS: Record<ContactSource, string> = {
  team_scrum_master: "The team's scrum master",
  team_manager: "The team's manager",
  member: "A named member",
};

export const MATRIX_SOURCE_LABELS: Record<MatrixSource, string> = {
  project: "Its own",
  tenant: "The tenant's",
  default: "The default",
};

export const MAX_LEVELS = 5;
const MAX_DAYS = 365;

export type LevelDraft = {
  label: string;
  source: ContactSource;
  memberId: string;
  /** Days per kind as typed; "" means the kind never reaches this level. */
  days: Record<NeedType, string>;
};

export type MatrixDraft = {
  decisionOwnerId: string;
  levels: LevelDraft[];
};

export function draftFromMatrix(matrix: EscalationMatrixResponse): MatrixDraft {
  return {
    decisionOwnerId: matrix.decision_owner_id ?? "",
    levels: matrix.levels.map(levelDraft),
  };
}

function levelDraft(level: EscalationLevelDto): LevelDraft {
  const days = level.after_days ?? {};
  return {
    label: level.label,
    source: level.source,
    memberId: level.member_id ?? "",
    days: {
      fix: days.fix === undefined ? "" : String(days.fix),
      decision: days.decision === undefined ? "" : String(days.decision),
      answer: days.answer === undefined ? "" : String(days.answer),
      review: days.review === undefined ? "" : String(days.review),
    },
  };
}

export function newLevel(after: LevelDraft | undefined): LevelDraft {
  const bump = (value: string) => (value === "" ? "" : String(Number(value) + 2));
  return {
    label: "",
    source: "member",
    memberId: "",
    days: after
      ? {
          fix: bump(after.days.fix),
          decision: bump(after.days.decision),
          answer: bump(after.days.answer),
          review: bump(after.days.review),
        }
      : { fix: "2", decision: "2", answer: "3", review: "2" },
  };
}

export function requestFromDraft(draft: MatrixDraft): EscalationMatrixRequest {
  return {
    decision_owner_id: draft.decisionOwnerId || null,
    levels: draft.levels.map((level) => ({
      label: level.label.trim().replace(/\s+/g, " "),
      source: level.source,
      member_id: level.source === "member" ? level.memberId || null : null,
      after_days: Object.fromEntries(
        NEEDS.filter((need) => level.days[need].trim() !== "").map((need) => [
          need,
          Number(level.days[need]),
        ]),
      ),
    })),
  };
}

/** What the server would refuse, said the same way, so the form says it first. */
export function draftProblems(draft: MatrixDraft): string[] {
  const problems: string[] = [];
  if (draft.levels.length > MAX_LEVELS) {
    problems.push(`A matrix has at most ${MAX_LEVELS} levels above the owner.`);
  }
  draft.levels.forEach((level, index) => {
    const number = index + 2;
    if (!level.label.trim()) problems.push(`Level ${number} needs a name.`);
    if (level.source === "member" && !level.memberId) {
      problems.push(`Level ${number} needs the member it goes to.`);
    }
    const bad = NEEDS.some((need) => {
      const value = level.days[need].trim();
      return value !== "" && !(/^\d+$/.test(value) && Number(value) <= MAX_DAYS);
    });
    if (bad) problems.push(`Level ${number} waits a whole number of days, 0 to ${MAX_DAYS}.`);
  });
  for (const need of NEEDS) {
    let last: { number: number; days: number } | null = null;
    draft.levels.forEach((level, index) => {
      const value = level.days[need].trim();
      if (!/^\d+$/.test(value)) return;
      const days = Number(value);
      if (last && days < last.days) {
        problems.push(
          `Level ${index + 2} is reached before level ${last.number} for ${need}: give it more days.`,
        );
      }
      last = { number: index + 2, days };
    });
  }
  return problems;
}

/** "Fix after 2 days, decision after 2, review after 2; never for answers." */
export function levelSummary(level: EscalationLevelDto): string {
  const days = level.after_days ?? {};
  const set = NEEDS.filter((need) => days[need] !== undefined);
  if (set.length === 0) return "Nothing reaches this level.";
  const parts = set.map((need, index) => {
    const value = days[need];
    const unit = index === 0 ? (value === 1 ? " day" : " days") : "";
    return `${NEED_LABELS[need].toLowerCase()} after ${value}${unit}`;
  });
  const never = NEEDS.filter((need) => days[need] === undefined).map(
    (need) => `${NEED_LABELS[need].toLowerCase()}s`,
  );
  const line = parts.join(", ");
  return (
    line[0].toUpperCase() +
    line.slice(1) +
    (never.length > 0 ? `; never for ${never.join(" or ")}` : "") +
    "."
  );
}
