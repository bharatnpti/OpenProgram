// Mock handlers for the Jira-observed day: Admin › Check-ins' switch "Fill the day
// from Jira for people who were not asked" (GET/PUT /config/checkin-fill), the
// "Jira today" line on task rows, and how a pod's day was captured. NOT real data:
// made-up keys and names from the demo roster, shapes from src/api/generated.ts,
// rules from backend core/domain/observed_day.py and core/application/fill_day.py.
// State resets when the mock restarts.

const TODAY = "2026-10-06";

const fill = { enabled: true, source: "default", updated_at: null, updated_by: null };
const fillBody = () => ({ ...fill, deployment_enabled: true });

/** What Jira says of a task today, by key; tasks it says nothing of have none. */
const OBSERVED = {
  "CHK-103": {
    status: "In Progress",
    stage: "in_development",
    since: "2026-09-28T08:40:00Z",
    moved_by: null,
    changes: [],
    quiet_days: 8,
  },
  "CHK-107": {
    status: "In Review",
    stage: "in_development",
    since: `${TODAY}T12:02:00Z`,
    moved_by: "Liam Chen",
    changes: ["due date moved Oct 7 → Oct 9"],
    quiet_days: null,
  },
  "CHK-108": {
    status: "In Progress",
    stage: "in_development",
    since: "2026-10-05T10:15:00Z",
    moved_by: null,
    changes: ["re-estimated 3 → 5"],
    quiet_days: null,
  },
  "CHK-099": {
    status: "Done",
    stage: "production",
    since: `${TODAY}T09:20:00Z`,
    moved_by: null,
    changes: [],
    quiet_days: null,
  },
};

/** A task row with what Jira says of it today. */
export function withObserved(task) {
  return { ...task, observed: OBSERVED[task.id] ?? null };
}

// How the board's people were captured today, beyond their own replies: some
// inferred from Jira and one with no data (no Jira link).
const INFERRED = {
  // Payments: silent after a nudge, inferred at the close-out.
  U1004: {
    summary:
      "No confirmed check-in after a nudge. Inferred from Jira and GitLab: CHK-104 moved to In Review (in development) and merge request !44 opened; CHK-099 resolved.",
    evidence: [
      "CHK-104 moved to In Review (in development) and merge request !44 opened",
      "CHK-099 resolved",
    ],
  },
  // Insights: not reached in chat, filled from Jira.
  U1010: {
    summary:
      "Not reached in chat. Inferred from Jira: INS-12 moved to In QA (in development → in testing).",
    evidence: ["INS-12 moved to In QA (in development → in testing)"],
  },
};
const NO_DATA = { U1009: "no_jira_link" };

/** A pod's board with its inferred days' evidence, its no-data causes and its coverage. */
export function withCoverage(board) {
  const developers = board.developers.map((d) => {
    const inferred = INFERRED[d.developer_id];
    if (inferred) {
      return {
        ...d,
        state: "stale",
        source: "inferred",
        status_as_of: TODAY,
        summary: inferred.summary,
        evidence: inferred.evidence,
        no_data_cause: null,
      };
    }
    const cause =
      d.status_as_of === TODAY && ["stale", "unknown"].includes(d.source)
        ? (NO_DATA[d.developer_id] ?? "nothing_moved")
        : null;
    return { ...d, evidence: [], no_data_cause: cause };
  });
  const today = developers.filter((d) => d.status_as_of === TODAY);
  const causes = today.map((d) => d.no_data_cause).filter(Boolean);
  const count = (state) => developers.filter((d) => d.state === state).length;
  return {
    ...board,
    confirmed: count("confirmed"),
    partial: count("partial"),
    stale: count("stale"),
    missing: count("missing"),
    developers,
    coverage: {
      replied: today.filter((d) => ["confirmed", "partial"].includes(d.source)).length,
      inferred: today.filter((d) => d.source === "inferred").length,
      no_data: causes.length,
      waiting: developers.length - today.length,
      causes: [...new Set(causes)].map((cause) => ({
        cause,
        count: causes.filter((c) => c === cause).length,
      })),
    },
  };
}

export function api(req, url, roles, userId, send, deny) {
  if (url.pathname !== "/config/checkin-fill") return false;
  if (!roles.includes("admin")) {
    deny();
    return true;
  }
  if (req.method === "GET") {
    send(200, fillBody());
    return true;
  }
  if (req.method === "PUT") {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      const body = JSON.parse(raw || "{}");
      if (typeof body.enabled !== "boolean") {
        send(422, {
          detail: [
            { type: "bool_type", loc: ["body", "enabled"], msg: "Input should be a valid boolean" },
          ],
        });
        return;
      }
      Object.assign(fill, {
        enabled: body.enabled,
        source: "tenant",
        updated_at: new Date().toISOString(),
        updated_by: userId ?? "U1001",
      });
      send(200, fillBody());
    });
    return true;
  }
  return false;
}
