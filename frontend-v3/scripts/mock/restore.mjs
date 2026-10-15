// Mock handlers for what the restored health views read beyond the console mock:
// a project's and a pod's health over 30 days (GET /persona/{project|pod}/{id}/trend),
// for the aggregate readers, as the backend serves it. NOT real data: a made-up
// month that turns from green to amber to red. Wired from ../mock-api.mjs.

const TODAY = "2026-10-06";
const aggregate = (roles) =>
  roles.some((role) => ["admin", "sm", "po", "mgr", "exec"].includes(role));

function trend(kind, id) {
  const points = Array.from({ length: 30 }, (_, i) => {
    const day = new Date(Date.UTC(2026, 8, 7 + i)).toISOString().slice(0, 10);
    // Weekends unreported, then green, amber and red as the month goes on.
    const weekend = [0, 6].includes(new Date(`${day}T12:00:00Z`).getUTCDay());
    const score = weekend ? 0 : i < 10 ? 3 : i < 22 ? 2 : 1;
    return {
      as_of: day,
      rag: ["unknown", "red", "amber", "green"][score],
      source: weekend ? "unknown" : "confirmed",
      score,
    };
  });
  return {
    entity_ref: { tenant_id: "demo", kind, id },
    window_days: 30,
    start: points[0].as_of,
    end: TODAY,
    points,
  };
}

export function api(req, url, roles, _userId, send, deny) {
  const match = url.pathname.match(/^\/persona\/(project|pod)\/([^/]+)\/trend$/);
  if (!match || req.method !== "GET") return false;
  if (!aggregate(roles)) {
    deny();
    return true;
  }
  send(200, trend(match[1], decodeURIComponent(match[2])));
  return true;
}
