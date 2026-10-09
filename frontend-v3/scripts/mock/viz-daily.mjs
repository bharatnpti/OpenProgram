// viz-daily lane: the day report's preview with its `facts`, for Daily's pictures.
//
// Two datasets, made up and shaped like the demo's Checkout Revamp on Fri 9 Oct:
// "short" (3 working days of history, no forecast yet) and "month" (a forecast, a
// date moved once). The whole-project report answers with the short one, or with
// the month when VIZ_DAILY_HISTORY=month or a `viz-history=month` cookie (the
// viz-overall mock's switch, so one server shows either dataset on both pages);
// the Release 1.0 report always with the month. `text` is rendered here the way the server's render_text renders the
// sections, so the preview drawer shows what a send would carry.

const DAY = "2026-10-09";
const SINCE = "2026-10-08";
const HISTORY = process.env.VIZ_DAILY_HISTORY === "month" ? "month" : "short";

/** The dataset a request asks for: its `viz-history` cookie, else the server's. */
function historyOf(req) {
  const cookie = /(?:^|;\s*)viz-history=(short|month)(?:;|$)/.exec(req.headers.cookie ?? "");
  return cookie ? cookie[1] : HISTORY;
}

const RAG_WORDS = {
  green: "On track",
  amber: "At risk",
  red: "Off track",
  unknown: "Status unknown",
};

function bar(percent) {
  const width = 20;
  if (percent === null || percent === undefined) return "░".repeat(width);
  const filled = Math.round((Math.max(0, Math.min(100, percent)) / 100) * width);
  return "█".repeat(filled) + "░".repeat(width - filled);
}

function tableLines(table) {
  return table.rows.map(
    (row) =>
      "• " +
      row
        .map((cell, index) => [table.columns[index], cell, index])
        .filter(([, cell]) => cell)
        .map(([column, cell, index]) => (index ? `${column}: ${cell}` : cell))
        .join(" · "),
  );
}

/** The server's render_text, line for line. */
function renderText(preview, reportId) {
  const lines = [
    preview.title,
    `${RAG_WORDS[preview.rag]}: ${preview.headline}`,
    "",
    `${bar(preview.percent_complete)} ${preview.progress_line}`,
  ];
  for (const section of preview.sections) {
    lines.push("", section.title.toUpperCase());
    const empty =
      !section.lines.length &&
      !section.groups.length &&
      !(section.table && section.table.rows.length);
    if (empty) {
      if (section.empty_text) lines.push(section.empty_text);
      continue;
    }
    lines.push(...section.lines.map((line) => `• ${line}`));
    for (const group of section.groups) {
      lines.push(group.heading, ...group.lines.map((line) => `  • ${line}`));
    }
    if (section.table) lines.push(...tableLines(section.table));
  }
  lines.push(
    "",
    `Open in OpenProgram: https://openprogram.example/reports/project-checkout/daily?report=${reportId}`,
    "",
    "Sent by OpenProgram.",
  );
  return lines.join("\n");
}

const ask = (need, text, waited, extra = {}) => ({
  need,
  text,
  detail: "",
  waited_days: waited,
  issue_key: null,
  escalated_to: null,
  escalation_label: null,
  needed_most: false,
  open_question: false,
  ...extra,
});
const toIra = { escalated_to: "Ira Novak", escalation_label: "Scrum master" };
const pr = (key, title, repo, open) =>
  `Pull request '${key} ${title}' in ${repo} has been open for ${open} days`;

const ASKS = [
  {
    heading: "Liam Chen",
    named: true,
    asks: [
      ask("review", "CHK-6: Noah Weber asked for a review", 5, {
        issue_key: "CHK-6",
        escalated_to: "Asha Rao",
        escalation_label: "Manager",
        needed_most: true,
      }),
    ],
  },
  {
    heading: "Zoe Almeida",
    named: true,
    asks: [
      ask("fix", pr("CHK-11", "Cart price breakdown", "acme/storefront-web", 7), 4, {
        ...toIra,
        needed_most: true,
      }),
      ask("fix", pr("CHK-12", "Promo code validation", "acme/storefront-web", 5), 2, toIra),
    ],
  },
  {
    heading: "Asha Rao",
    named: true,
    asks: [ask("review", "Mina Patel asked for a review", 6, { ...toIra, needed_most: true })],
  },
  {
    heading: "Noah Weber",
    named: true,
    asks: [ask("fix", pr("CHK-6", "Refund edge cases", "acme/checkout-api", 7), 4, toIra)],
  },
  {
    heading: "Sofia Bergmann",
    named: true,
    asks: [
      ask("fix", pr("CHK-14", "Test plan: guest checkout", "acme/storefront-web", 7), 4, toIra),
    ],
  },
  {
    heading: "Mina Patel",
    named: true,
    asks: [
      ask(
        "answer",
        'CHK-12: "Does a promo code apply to the subtotal only, or to the shipping fee as well?"',
        3,
        {
          detail: "asked by Sofia Bergmann (partly answered)",
          issue_key: "CHK-12",
          open_question: true,
        },
      ),
    ],
  },
  {
    heading: "Nobody named yet",
    named: false,
    asks: [
      ask("review", "CHK-12: keep or dismiss 2 acceptance criteria read from Jira", 3, {
        issue_key: "CHK-12",
      }),
      ask("review", "Omar Haddad asked for a review", 2),
    ],
  },
];

/** An ask as the report words it under its owner. */
function askLine(item) {
  const context = [
    item.waited_days === null ? "" : item.waited_days ? `${item.waited_days} days` : "since today",
    item.detail,
  ].filter(Boolean);
  const words = { fix: "Fix", decision: "Decision", answer: "Answer", review: "Review" };
  let line = `${words[item.need]}: ${item.text}${context.length ? ` (${context.join("; ")})` : ""}.`;
  if (item.escalated_to) line += ` Escalated to ${item.escalated_to} (${item.escalation_label}).`;
  return line;
}

const BYPASSED = [
  { key: "CHK-12", stage: "business_testing", gates: ["Engineering delivery"] },
  ...["CHK-13", "CHK-17", "CHK-3", "CHK-5", "CHK-8"].map((key) => ({
    key,
    stage: "production",
    gates: ["Business acceptance", "Engineering delivery"],
  })),
];

function preview(history, release) {
  const month = history === "month";
  const name = release ? "Checkout Revamp, Release 1.0" : "Checkout Revamp";
  const reportId = release ? "rep-checkout-r1" : "rep-checkout";
  const progress = "28% complete: 5 of 18 requirements in production (28% on Thu 8 Oct 2026).";
  const delivery = month
    ? "Delivery Tue 15 Dec 2026: at risk. History says 85% likely by Mon 21 Dec 2026. The team's latest date is Fri 9 Oct 2026 (CHK-4)."
    : "Delivery Tue 15 Dec 2026: at risk. The team's latest date is Fri 9 Oct 2026 (CHK-4).";
  const reasons = month
    ? [
        "Committed for Tue 15 Dec 2026 by Mina Patel; moved once, 14 days later than first set.",
        "History: 50% likely by Wed 2 Dec 2026, 85% by Mon 21 Dec 2026 (13 requirements to go; 5 finished in the last 15 working days).",
        "Team dates: the latest open requirement is due Fri 9 Oct 2026 (CHK-4).",
      ]
    : [
        "Committed for Tue 15 Dec 2026 by Mina Patel.",
        "Only 3 working days of history; a forecast needs 10.",
        "Team dates: the latest open requirement is due Fri 9 Oct 2026 (CHK-4).",
      ];
  const bypassLines = BYPASSED.map(
    (item) =>
      `${item.key} reached ${item.stage.replace("_", " ")} without ${item.gates.join(" and ")} passing.`,
  );
  const important = [...reasons, ...bypassLines].slice(0, 8);
  const body = {
    title: `${name}: day report, Fri 9 Oct 2026`,
    report_date: DAY,
    rag: "amber",
    headline: "4 fixes, 1 answer and 4 reviews needed, 6 escalated.",
    percent_complete: 27.8,
    progress_line: progress,
    console_path: `/reports/project-checkout/daily?report=${reportId}`,
    sections: [
      {
        title: "In short",
        lines: [
          "Mina Patel: The business agreed the refund criteria today. I answer the promo code question on Monday.",
          delivery,
          "Needed most: a review from Liam Chen on CHK-6 (5 days), a review from Asha Rao (6 days) and a fix from Zoe Almeida (4 days).",
        ],
        groups: [],
        table: null,
        empty_text: "",
      },
      {
        title: "Where we stand",
        lines: [],
        groups: [
          {
            heading: "Progress",
            lines: [
              progress,
              "Raised 6 (-1) · Groomed 0 · In development 6 · In testing 0 · Business testing 1 (+1) · Production 5",
            ],
          },
          {
            heading: "What changed since Thu 8 Oct 2026",
            lines: [
              "CHK-16 Q4 checkout roadmap review: Raised → In development",
              "CHK-12 Promo code validation: In development → Business testing",
            ],
          },
          {
            heading: "Acceptance and tests",
            lines: [
              "Business acceptance (before production): 1 of 18 passed, 17 with nothing confirmed; 5 moved on without it.",
              "Engineering delivery (before business testing): 0 of 18 passed, 18 with nothing confirmed; 6 moved on without it.",
            ],
          },
        ],
        table: null,
        empty_text: "",
      },
      {
        title: "Most important",
        lines: [...important, "and 5 more."],
        groups: [],
        table: null,
        empty_text: "Nothing threatens the delivery date today.",
      },
      {
        title: "What we need, and from whom",
        lines: [],
        groups: ASKS.map((owner) => ({
          heading: owner.heading,
          lines: owner.asks.map(askLine),
        })),
        table: null,
        empty_text: "Nothing is needed from anyone today.",
      },
      {
        title: "Open questions",
        lines: [],
        groups: [],
        table: {
          columns: ["Ticket", "What we asked", "Asked to", "Asked on", "Heard back?"],
          rows: [
            [
              "CHK-12",
              "Does a promo code apply to the subtotal only, or to the shipping fee as well?",
              "Mina Patel",
              "Tue 6 Oct 2026",
              "Partly",
            ],
          ],
        },
        empty_text: "No open questions.",
      },
    ],
    facts: {
      note: {
        author: "Mina Patel",
        text: "The business agreed the refund criteria today. I answer the promo code question on Monday.",
      },
      delivery: {
        verdict: "at_risk",
        target: "2026-12-15",
        target_source: "committed",
        committed_by: "Mina Patel",
        times_moved: month ? 1 : 0,
        moved_days: month ? 14 : null,
        p50: month ? "2026-12-02" : null,
        p85: month ? "2026-12-21" : null,
        history_days: month ? null : 3,
        history_needed: month ? null : 10,
        no_forecast_reason: month ? null : "Only 3 working days of history; a forecast needs 10.",
        team_latest: DAY,
        team_latest_key: "CHK-4",
      },
      progress: {
        percent: 27.8,
        since: SINCE,
        total: 18,
        stages: [
          { stage: "raised", count: 6, previous: 7 },
          { stage: "groomed", count: 0, previous: 0 },
          { stage: "in_development", count: 6, previous: 6 },
          { stage: "in_testing", count: 0, previous: 0 },
          { stage: "business_testing", count: 1, previous: 0 },
          { stage: "production", count: 5, previous: 5 },
        ],
        moves: [
          {
            key: "CHK-16",
            title: "Q4 checkout roadmap review",
            from_stage: "raised",
            to_stage: "in_development",
          },
          {
            key: "CHK-12",
            title: "Promo code validation",
            from_stage: "in_development",
            to_stage: "business_testing",
          },
        ],
        more_moves: 0,
        other_changes: [],
        notes: [],
      },
      gates: [
        {
          name: "Business acceptance",
          guards_stage: "production",
          total: 18,
          passed: 1,
          bypassed: 5,
          failed: 0,
          open: 0,
          missing: 12,
        },
        {
          name: "Engineering delivery",
          guards_stage: "business_testing",
          total: 18,
          passed: 0,
          bypassed: 6,
          failed: 0,
          open: 0,
          missing: 12,
        },
      ],
      important: {
        drawn: month ? ["committed", "forecast", "team"] : ["committed", "history", "team"],
        bypassed: BYPASSED,
        risks: 4,
        lines: [],
      },
      asks: ASKS,
    },
  };
  return { ...body, text: renderText(body, reportId) };
}

export function api(req, url, _roles, _userId, send) {
  const match = /^\/day-reports\/([^/]+)\/preview$/.exec(url.pathname);
  if (!match || req.method !== "GET") return false;
  const release = match[1].endsWith("-r1");
  send(200, preview(release ? "month" : historyOf(req), release));
  return true;
}
