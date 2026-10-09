// Mock handler for the assistant. NOT real data: shapes follow
// src/api/generated.ts, names follow the seeded demo tenant. Called by
// ../mock-api.mjs before the reports lane's handlers.
//
// POST /ask answers from the question's words, single-turn like the server
// (core/application/ask_service.py): a team or executive aggregate reader only
// (403 for a developer, in the router's own words), as of the day sent.

const AGGREGATE = ["sm", "po", "mgr", "exec", "admin"];

/** Answers and says so: the router stops at the first handler that returns true. */
const reply = (send, status, body) => {
  send(status, body);
  return true;
};

function readBody(req) {
  return new Promise((resolve) => {
    let data = "";
    req.on("data", (chunk) => (data += chunk));
    req.on("end", () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch {
        resolve({});
      }
    });
  });
}

const dayWords = (iso) =>
  new Date(`${iso}T12:00:00Z`).toLocaleDateString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "short",
    timeZone: "UTC",
  });

/** A made-up answer that fits the question's words, with the sources it names. */
export function answerFor(question, asOf) {
  const q = String(question ?? "").toLowerCase();
  const when = asOf ? ` As of ${dayWords(asOf)}.` : "";
  const checkout = { id: "project-checkout", kind: "project", label: "Checkout Revamp" };
  const payments = { id: "pod-payments", kind: "pod", label: "Payments Pod" };
  const blocked = { id: "CHK-103", kind: "task", label: "3-D Secure step-up" };
  let answer;
  let sources;
  if (/replied|checked in|check-in/.test(q)) {
    answer =
      "Two people in Payments Pod have not replied yet: Kai Thompson (no reply since Mon 5 Oct) and Noah Weber (partly replied, the ETA is open). Everyone else gave their status in chat.";
    sources = [payments, { id: "U1007", kind: "developer", label: "Kai Thompson" }];
  } else if (/block/.test(q)) {
    answer =
      "The oldest open blocker is the 3-D Secure step-up (CHK-103): waiting 9 days on sandbox credentials from the Platform team. It holds Payments Pod's date of Tue 3 Nov.";
    sources = [blocked, payments];
  } else if (/no eta|no committed|due date/.test(q)) {
    answer =
      "Customer Insights has no committed date, and 2 of its open requirements carry no ETA or due date. Checkout Revamp has 3 undated requirements.";
    sources = [{ id: "project-insights", kind: "project", label: "Customer Insights" }, checkout];
  } else if (/review|merge request/.test(q)) {
    answer =
      "Merge requests wait longest in review on the payments-service repository: 31 hours at the median over the last 14 days.";
    sources = [{ id: "repo-payments", kind: "repo", label: "payments-service" }];
  } else {
    answer =
      "Checkout Revamp is off track: history puts the finish at Wed 4 Nov (50% likely), after the committed Fri 30 Oct. The 3-D Secure step-up (CHK-103) has been blocked for 9 days. Identity Platform is on track for Fri 20 Nov.";
    sources = [
      checkout,
      { id: "project-identity", kind: "project", label: "Identity Platform" },
      blocked,
    ];
  }
  return {
    answer: `${answer}${when}`,
    references: sources.map((source) => source.id),
    tools_used: ["graph_search", "open_risks"],
    trace_id: "mock",
    sources,
  };
}

export function api(req, url, roles, userId, send) {
  const p = url.pathname.replace(/^\/api\/v1/, "");
  const method = req.method ?? "GET";
  const has = (allowed) => roles.some((role) => allowed.includes(role));

  if (p === "/ask" && method === "POST") {
    if (!has(AGGREGATE)) {
      return reply(send, 403, { detail: `${userId} is not authorized for aggregate read` });
    }
    void readBody(req).then((body) => {
      const question = String(body?.question ?? "").trim();
      if (!question) {
        send(422, { detail: [{ loc: ["body", "question"], msg: "Field required" }] });
        return;
      }
      // A moment's wait, so the "looking it up" bubble shows as it does live.
      setTimeout(() => send(200, answerFor(question, body?.as_of ?? null)), 400);
    });
    return true;
  }

  return false;
}
