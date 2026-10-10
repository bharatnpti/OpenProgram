// Only type imports here: node --test strips types but cannot resolve
// extensionless runtime imports.
import type {
  JiraCreateProjectsDto,
  JiraCreateProjectsUpdate,
  JiraCreateProjectsValueDto,
  JiraWriteKind,
  JiraWriteKindDto,
  JiraWriteSettingSource,
  JiraWriteSwitchDto,
  JiraWritesChangeDto,
  JiraWritesResponse,
  JiraWritesUpdateRequest,
} from "../../api/schema";

/*
 * Admin › Jira writes, in words. Each switch says what it allows and what still
 * guards it. Limits and sentences mirror core/domain/jira_writes.py, so the form
 * says what the server would refuse before it is sent; the server still decides.
 */

/** The one read of the switches: Admin › Jira writes, Check-ins and Release readiness share it. */
export const JIRA_WRITES_KEY = ["config", "jira-writes"] as const;

export const MASTER_LABEL = "Jira writes for this tenant";
export const PROJECTS_LABEL = "Projects new issues may be created in";
export const MAX_PROJECTS = 20;
const PROJECT_KEY = /^[A-Z][A-Z0-9_]+$/;

export const KIND_ORDER: JiraWriteKind[] = ["checkin_updates", "console_moves", "readiness_create"];

export const KIND_WORDS: Record<
  JiraWriteKind,
  { label: string; allows: string; guards: string[]; byDefault: string }
> = {
  checkin_updates: {
    label: "Update tickets from check-ins",
    allows:
      "When a member's check-in says a ticket has moved (started, in review, done), OpenProgram moves it in Jira and adds a comment saying so.",
    guards: [
      "Each member's write-back consent: asked each time (the default), applied without asking, or never.",
      "Only the ticket's assignee: a ticket assigned to someone else, or to nobody, is never changed.",
      "A ticket is not moved to done while a merge request for it is still open.",
    ],
    byDefault: "On until someone turns it off, as before this switch existed.",
  },
  console_moves: {
    label: "Move tickets from task updates",
    allows:
      "When a person updates their own task in OpenProgram and ticks “Also move in Jira”, OpenProgram moves that ticket.",
    guards: [
      "The person's own tick, each time. A member whose consent is never is not offered it.",
      "Only a ticket assigned to them in Jira.",
      "A ticket is not moved to done while a merge request for it is still open.",
    ],
    byDefault: "On until someone turns it off, as before this switch existed.",
  },
  readiness_create: {
    label: "Create release-readiness issues",
    allows:
      "When a person presses Create on a drafted release-readiness issue, OpenProgram creates it in Jira: unassigned, naming who approved it.",
    guards: [
      "A person pressing Create on one draft. The agent never creates an issue by itself.",
      "Only in the projects listed under “Projects new issues may be created in”.",
    ],
    byDefault: "Off until an admin turns it on.",
  },
};

export function kindOf(writes: JiraWritesResponse, kind: JiraWriteKind): JiraWriteKindDto {
  return (
    writes.kinds.find((item) => item.kind === kind) ?? {
      kind,
      on: false,
      source: "default",
      effective: false,
    }
  );
}

/** The request that sets one kind's switch, and nothing else. */
export function kindBody(kind: JiraWriteKind, on: boolean): JiraWritesUpdateRequest {
  if (kind === "checkin_updates") return { checkin_updates: on };
  if (kind === "console_moves") return { console_moves: on };
  return { readiness_create: on };
}

/** Where a value comes from: "The default.", "Set in the deployment's settings.", "Set by an admin." */
export function sourceWords(source: JiraWriteSettingSource): string {
  if (source === "env") return "Set in the deployment's settings.";
  if (source === "admin") return "Set by an admin.";
  return "The default.";
}

/** The master switch's line under its name. */
export function masterLine(master: JiraWriteSwitchDto): string {
  return master.on
    ? "OpenProgram may write to Jira, for the kinds switched on below, each behind its own guards."
    : "Nothing is written to Jira, whatever is switched on below.";
}

/** What a kind does now, given the master switch. */
export function kindStateLine(kind: JiraWriteKindDto, master: JiraWriteSwitchDto): string {
  if (!kind.on) return "Off: nothing of this kind is written to Jira.";
  if (!master.on) return `On, but nothing is written while ${MASTER_LABEL} is off.`;
  return "On: written to Jira, behind the guards below.";
}

export const CONFIRM_MASTER_ON = {
  title: `Turn on ${MASTER_LABEL}?`,
  description:
    "OpenProgram will start writing to this tenant's Jira, but only for the kinds switched on below, and each behind its own guards: a member's consent, the ticket's owner, an open merge request, a person pressing Create. Turning it off again stops new writes; it does not undo the ones made.",
  confirmLabel: "Turn on Jira writes",
};

/** "the scope's own project", "the scope's own project and SEC, OPS", "only SEC". */
export function projectsWords(value: JiraCreateProjectsValueDto | JiraCreateProjectsDto): string {
  const keys = value.projects.join(", ");
  if (value.own_project) {
    return keys ? `the scope's own Jira project and ${keys}` : "the scope's own Jira project";
  }
  return keys ? `only ${keys}` : "no project";
}

/** Typed project keys, as the server tidies them: capitals, no repeats, no blanks. */
export function parseProjectKeys(text: string): string[] {
  const keys = text
    .split(/[\s,;]+/)
    .map((key) => key.trim().toUpperCase())
    .filter(Boolean);
  return [...new Set(keys)];
}

/** What stops a save of the allowed projects, in the server's words. */
export function projectProblems(ownProject: boolean, text: string): string[] {
  const keys = parseProjectKeys(text);
  const problems: string[] = [];
  const bad = keys.filter((key) => !PROJECT_KEY.test(key));
  if (bad.length > 0) {
    problems.push(
      `A Jira project key is capital letters and digits, such as CHK: ${bad.join(", ")}.`,
    );
  }
  if (keys.length > MAX_PROJECTS) problems.push(`List at most ${MAX_PROJECTS} other projects.`);
  if (!ownProject && keys.length === 0) {
    problems.push(
      "Allow at least one project: the scope's own, or a project key. To stop creating issues, turn off Create release-readiness issues.",
    );
  }
  return problems;
}

/** The request body for the allowed projects, or null when nothing changed. */
export function projectsBody(
  current: JiraCreateProjectsDto,
  ownProject: boolean,
  text: string,
): JiraCreateProjectsUpdate | null {
  const projects = parseProjectKeys(text);
  const same =
    ownProject === current.own_project &&
    projects.length === current.projects.length &&
    projects.every((key, index) => key === current.projects[index]);
  return same ? null : { own_project: ownProject, projects };
}

function settingLabel(setting: string): string {
  if (setting === "master") return MASTER_LABEL;
  if (setting === "create_projects") return PROJECTS_LABEL;
  return KIND_WORDS[setting as JiraWriteKind]?.label ?? setting;
}

const onOff = (on: boolean | null | undefined) => (on ? "on" : "off");

const SOURCE_NOUN: Record<JiraWriteSettingSource, string> = {
  default: "the default",
  env: "from the deployment's settings",
  admin: "set by an admin",
};

/**
 * One change in the history: "Asha Rao turned Create release-readiness issues on
 * (it was off, the default)." A value set to what it already was is "set to",
 * since it no longer follows the default.
 */
export function changeLine(change: JiraWritesChangeDto): string {
  const who = change.by_name ?? change.by;
  const what = settingLabel(change.setting);
  const before = change.before_projects
    ? projectsWords(change.before_projects)
    : onOff(change.before_on);
  const after = change.after_projects
    ? projectsWords(change.after_projects)
    : onOff(change.after_on);
  const was = `it was ${before}, ${SOURCE_NOUN[change.before_source]}`;
  if (change.after_projects || before === after) return `${who} set ${what} to ${after} (${was}).`;
  return `${who} turned ${what} ${after} (${was}).`;
}

/** Release readiness's line about creating issues: the effective state, in words. */
export function readinessCreateLine(writes: JiraWritesResponse): string {
  const create = kindOf(writes, "readiness_create");
  const projects = projectsWords(writes.create_projects);
  if (!create.on) {
    return "Create release-readiness issues is off: drafts stay drafts, and nothing is created in Jira.";
  }
  if (!writes.master.on) {
    return `Create release-readiness issues is on, but ${MASTER_LABEL} is off, so nothing is created in Jira.`;
  }
  return `Create release-readiness issues is on: a person can press Create on a draft, and the issue goes to ${projects}.`;
}

/** The Check-ins tab's line about write-back: the effective state of updates from check-ins. */
export function checkinWritebackLine(writes: JiraWritesResponse): string {
  const checkins = kindOf(writes, "checkin_updates");
  if (!writes.master.on) {
    return `${MASTER_LABEL} is off: consent is recorded, but no check-in changes a Jira ticket.`;
  }
  if (!checkins.on) {
    return "Update tickets from check-ins is off: consent is recorded, but no check-in changes a Jira ticket.";
  }
  return "Update tickets from check-ins is on: a member's consent decides whether their check-in moves their Jira tickets.";
}
