// Mock handlers for the console IA lane T (Today and Check-in): the Jira changes
// waiting for a person's yes and their answer (GET /me/jira-changes, POST
// /me/jira-changes/{id}/answer), the person's own conversation (GET
// /me/conversation), who must act on a project (GET /projects/{id}/asks) and
// the day's counts (GET /me/today-counts). NOT real data: shapes follow
// src/api/generated.ts, people follow the seeded demo roster. Wired from
// ../mock-api.mjs before the generic console router.
//
// Kai (U1007) has a held check-in proposal (CHK-103 -> Done), a merged-but-open
// suggestion (CHK-105) and a due date his ETA is past (CHK-104); a yes or no
// sticks, so the round trip can be watched. An executive's asks name the team,
// never a person; a developer reads no counts and no asks (403), as the API does.

const TODAY = "2026-10-06";
const has = (roles, ...wanted) => roles.some((role) => wanted.includes(role));
const reply = (send, status, body) => {
  send(status, body);
  return true;
};

const gate = (state, mergeRequests = []) => ({
  state,
  yes_allowed: state === "open" || state === "no_change",
  tracker_status: "In Progress",
  merge_requests: mergeRequests,
});

/** Each person's changes, by member id; an answer removes the row. */
const CHANGES = {
  U1007: [
    {
      id: "proposal:mock-chk103",
      kind: "proposal",
      issue_key: "CHK-103",
      issue_title: "3-D Secure step-up flow",
      why: "You said it is done in today's check-in.",
      asked_on: TODAY,
      target_label: "Done",
      gate: gate("open"),
      answerable: true,
      holds_checkin: true,
      due_on: null,
      eta_on: null,
    },
    {
      id: "suggestion:CHK-105",
      kind: "suggestion",
      issue_key: "CHK-105",
      issue_title: "Cart total rounding",
      why: "'Cart total rounding' in acme/checkout-api was merged 2 days ago, but CHK-105 is still 'In Progress' in the issue tracker.",
      asked_on: TODAY,
      target_label: "Done",
      gate: gate("open"),
      answerable: true,
      holds_checkin: false,
      due_on: null,
      eta_on: null,
    },
    {
      id: "due:CHK-104",
      kind: "due_date",
      issue_key: "CHK-104",
      issue_title: "Capture retry runbook",
      why: "You gave Oct 14 as its ETA; its due date in Jira is Oct 9.",
      asked_on: null,
      target_label: null,
      gate: null,
      answerable: false,
      holds_checkin: false,
      due_on: "2026-10-09",
      eta_on: "2026-10-14",
    },
  ],
};

const ASKS = [
  {
    need: "fix",
    need_label: "Fix",
    what: "CHK-102: refund edge cases wait on the finance sign-off",
    who: "Ira Novak",
    team: "Payments Pod",
    waited_days: 9,
    detail: "reported by Noah Weber",
    issue_key: "CHK-102",
    level: 2,
    escalated_to: "Asha Rao",
    escalation_label: "manager",
  },
  {
    need: "review",
    need_label: "Review",
    what: "CHK-101: Liam Chen asked for a review",
    who: "Noah Weber",
    team: "Payments Pod",
    waited_days: 3,
    detail: null,
    issue_key: "CHK-101",
    level: 1,
    escalated_to: null,
    escalation_label: null,
  },
  {
    need: "answer",
    need_label: "Answer",
    what: "CHK-203: Zoe Almeida asked for input",
    who: "Mina Patel",
    team: "Storefront Pod",
    waited_days: 1,
    detail: null,
    issue_key: "CHK-203",
    level: 1,
    escalated_to: null,
    escalation_label: null,
  },
];
const NAMES = ["Ira Novak", "Noah Weber", "Liam Chen", "Zoe Almeida", "Mina Patel", "Asha Rao"];

/** An executive's row: the team for who, no detail or escalation names, names out of the text. */
function forExecutive(ask) {
  let what = ask.what;
  for (const name of NAMES) what = what.replaceAll(name, "a team member");
  return { ...ask, what, who: ask.team, detail: null, escalated_to: null };
}

export function api(req, url, roles, userId, send, deny) {
  const p = url.pathname;
  if (p === "/me/jira-changes" && req.method === "GET") {
    return reply(send, 200, { as_of: TODAY, tracker: "Jira", items: CHANGES[userId] ?? [] });
  }
  const answer = /^\/me\/jira-changes\/([^/]+)\/answer$/.exec(p);
  if (answer && req.method === "POST") {
    const id = decodeURIComponent(answer[1]);
    const items = CHANGES[userId] ?? [];
    const item = items.find((entry) => entry.id === id);
    if (!item) return reply(send, 404, { detail: `No change '${id}' is waiting for you.` });
    if (item.kind === "due_date") {
      return reply(send, 422, {
        detail: "Change the due date in Jira: OpenProgram never writes a due date.",
      });
    }
    let body = "";
    req.on("data", (chunk) => (body += chunk));
    req.on("end", () => {
      const choice = JSON.parse(body || "{}").answer;
      if (choice !== "yes" && choice !== "no") {
        send(422, { detail: [{ loc: ["body", "answer"], msg: "Input should be 'yes' or 'no'" }] });
        return;
      }
      CHANGES[userId] = items.filter((entry) => entry.id !== id);
      send(200, {
        item_id: id,
        issue_key: item.issue_key,
        outcome: choice === "yes" ? "applied" : "declined",
        detail:
          choice === "yes"
            ? `Moved ${item.issue_key} to ${item.target_label} in Jira.`
            : `Left ${item.issue_key} as it is in Jira.`,
        checkin_finished: item.holds_checkin,
        line_sent: item.kind === "proposal",
      });
    });
    return true;
  }
  if (p === "/me/conversation") {
    const day = url.searchParams.get("day") ?? TODAY;
    if (day < "2026-09-06") {
      return reply(send, 200, {
        day,
        kept: false,
        retention_days: 30,
        note: "Not kept after 30 days.",
        turns: [],
      });
    }
    const turns =
      day === TODAY && userId === "U1007"
        ? [
            {
              role: "agent",
              text: "Morning Kai. Quick check-in on CHK-103 and CHK-104: where do they stand?",
              at: `${TODAY}T07:30:00Z`,
            },
            {
              role: "user",
              text: "CHK-103 is done and merged. CHK-104 slips to the 14th.",
              at: `${TODAY}T07:41:00Z`,
            },
          ]
        : [];
    return reply(send, 200, { day, kept: true, retention_days: 30, note: null, turns });
  }
  const asks = /^\/projects\/([^/]+)\/asks$/.exec(p);
  if (asks) {
    if (!has(roles, "po", "mgr", "exec", "admin", "sm")) return deny();
    const executive = has(roles, "exec") && !has(roles, "admin", "mgr", "po", "sm");
    return reply(send, 200, {
      project_id: decodeURIComponent(asks[1]),
      project_name: "Checkout Revamp",
      as_of: TODAY,
      names_hidden: executive,
      asks: executive ? ASKS.map(forExecutive) : ASKS,
    });
  }
  if (p === "/me/today-counts") {
    if (!has(roles, "sm", "po", "mgr", "exec", "admin")) return deny();
    const portfolio = has(roles, "mgr", "exec", "admin");
    return reply(send, 200, {
      as_of: TODAY,
      scope: portfolio ? "portfolio" : "own",
      replies: portfolio
        ? { people: 14, replied: 8, inferred: 3, waiting: 3 }
        : { people: 4, replied: 2, inferred: 1, waiting: 1 },
      open_warnings: portfolio ? 6 : 2,
      project_count: portfolio ? 3 : 1,
    });
  }
  return false;
}
