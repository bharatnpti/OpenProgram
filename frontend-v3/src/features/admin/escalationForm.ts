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

/*
 * The escalation matrix editor. Rules and wording mirror
 * core/domain/escalation_matrix.py (validated_matrix), so the form says what
 * the server would refuse before it is sent.
 */

export const NEEDS: NeedType[] = ["fix", "decision", "answer", "review"];

export const NEED_LABELS: Record<NeedType, string> = {
  fix: "Fix",
  decision: "Decision",
  answer: "Answer",
  review: "Review",
};

const NEED_WITH_ARTICLE: Record<NeedType, string> = {
  fix: "a fix",
  decision: "a decision",
  answer: "an answer",
  review: "a review",
};

export const NEED_HINTS: Record<NeedType, string> = {
  fix: "Something blocked or broken to put right",
  decision: "A call only its owner can make, such as accepting a requirement",
  answer: "A question waiting for a reply",
  review: "Work waiting to be looked at: a code review, suggestions to keep",
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
const MAX_LABEL = 60;

export type LevelDraft = {
  label: string;
  source: ContactSource;
  memberId: string;
  /** Days per kind as typed; "" means that kind never reaches this level. */
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
  const text = (need: NeedType) => (days[need] === undefined ? "" : String(days[need]));
  return {
    label: level.label,
    source: level.source,
    memberId: level.member_id ?? "",
    days: {
      fix: text("fix"),
      decision: text("decision"),
      answer: text("answer"),
      review: text("review"),
    },
  };
}

/** A new level starts two days after the one below it, so it is never reached first. */
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

export function sameMatrix(left: MatrixDraft, right: MatrixDraft): boolean {
  return JSON.stringify(requestFromDraft(left)) === JSON.stringify(requestFromDraft(right));
}

/** What the server would refuse, said first by the form. */
export function draftProblems(draft: MatrixDraft): string[] {
  const problems: string[] = [];
  if (draft.levels.length > MAX_LEVELS) {
    problems.push(`A matrix has at most ${MAX_LEVELS} levels above the owner.`);
  }
  draft.levels.forEach((level, index) => {
    const number = index + 2;
    const label = level.label.trim();
    if (!label) problems.push(`Level ${number} needs a name.`);
    else if (label.length > MAX_LABEL) {
      problems.push(`Level ${number}'s name is longer than ${MAX_LABEL} characters.`);
    }
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
          `Level ${index + 2} is reached before level ${last.number} for ${NEED_WITH_ARTICLE[need]}: give it more days.`,
        );
      }
      last = { number: index + 2, days };
    });
  }
  return problems;
}

/** "Fix after 2 days, decision after 2, answer after 3; never for reviews." */
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
