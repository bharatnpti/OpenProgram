// Mock of one pod's part of a project: `pod_id` on /portfolio/warnings and
// /portfolio/scope, on /projects/{id}/requirements and /projects/{id}/gates, and
// GET /pods/{id}/readiness. NOT real data: it narrows what the other mocks answer
// for the whole project, by the made-up rule that a CHK-1xx key is the Payments
// Pod's and a CHK-2xx key the Storefront Pod's. Every warning item names its
// pods, and its owner's last words where the reader sees the owner, as the
// backend does (core/application/portfolio_warnings_service.py).
import * as portfolioMock from "./portfolio.mjs";
import * as releaseReadiness from "./release-readiness.mjs";
import * as reportsLane from "./reports.mjs";

const PODS = {
  "pod-payments": { name: "Payments Pod", project: "project-checkout", people: 6 },
  "pod-storefront": { name: "Storefront Pod", project: "project-checkout", people: 5 },
  "pod-identity": { name: "Identity Pod", project: "project-identity", people: 4 },
  "pod-data": { name: "Data Pod", project: "project-insights", people: 3 },
};
const OWN_POD = {
  U1002: "pod-payments",
  U1004: "pod-payments",
  U1007: "pod-payments",
  U1009: "pod-payments",
  U1005: "pod-storefront",
  U1012: "pod-storefront",
};
const SAYS = {
  "Kai Thompson": "Payment intent API is in review; waiting on one approval.",
  "Ben Sorensen": "Refund rounding confirmed with finance, rolling out tomorrow.",
};

const has = (roles, ...wanted) => roles.some((role) => wanted.includes(role));

/** The pod a key sits under, by the mock's rule. */
function podOfKey(key) {
  if (/^CHK-1\d\d$/.test(key) || key === "CHK-206") return "pod-payments";
  if (/^CHK-2\d\d$/.test(key)) return "pod-storefront";
  if (/^IDP-/.test(key)) return "pod-identity";
  if (/^INS-/.test(key)) return "pod-data";
  return null;
}

/** What another mock answers for the same request, without sending it. */
function capture(handler, req, url, roles, userId) {
  let out = null;
  const handled = handler(
    req,
    url,
    roles,
    userId,
    (status, body) => (out = { status, body }),
    () => (out = { status: 403, body: { detail: "Not allowed." } }),
  );
  return handled ? out : null;
}

function refuse(send, status, detail) {
  send(status, { detail });
  return true;
}

/** A pod id the reader may read on its own; else the refusal has been sent. */
function checkPod(podId, projectId, roles, userId, send) {
  const pod = PODS[podId];
  if (!pod) return refuse(send, 404, `No pod '${podId}'.`) && null;
  if (projectId && pod.project !== projectId) {
    const names = {
      "project-checkout": "Checkout Revamp",
      "project-identity": "Identity Platform",
      "project-insights": "Insights",
    };
    return (
      refuse(send, 404, `${pod.name} doesn't work on ${names[projectId] ?? projectId}.`) && null
    );
  }
  if (roles.length === 1 && roles[0] === "exec" && pod.people < 5) {
    return (
      refuse(send, 403, "A pod of fewer than 5 people is read as part of its project.") && null
    );
  }
  return pod;
}

function withPods(body, roles) {
  const named = has(roles, "sm", "mgr", "admin");
  return {
    ...body,
    groups: body.groups.map((group) => ({
      ...group,
      items: group.items.map((item) => {
        const podId = podOfKey(item.key);
        return {
          ...item,
          pods: podId ? [{ id: podId, name: PODS[podId].name }] : [],
          owner_says:
            named && item.owner && SAYS[item.owner]
              ? { text: SAYS[item.owner], source: "confirmed", on: body.as_of }
              : null,
        };
      }),
    })),
  };
}

function narrowedWarnings(body, podId) {
  const groups = body.groups.map((group) => {
    const items = group.items.filter((item) => item.pods.some((pod) => pod.id === podId));
    const count = group.counts_only ? (podId === "pod-payments" ? group.count : 0) : items.length;
    return { ...group, items, count };
  });
  return {
    ...body,
    groups,
    total: groups.reduce((sum, group) => sum + group.count, 0),
    projects: 1,
  };
}

export function api(req, url, roles, userId, send) {
  const p = url.pathname;
  const podId = url.searchParams.get("pod_id");
  const projectId = url.searchParams.get("project_id");
  let m;

  if (p === "/portfolio/warnings") {
    const ownPod = OWN_POD[userId];
    const asMember = podId && has(roles, "dev") && !has(roles, "po", "sm", "mgr", "exec", "admin");
    if (asMember && ownPod !== podId) {
      return refuse(send, 403, "You read your own pod's details, and this is not your pod.");
    }
    const lens = asMember ? ["po"] : roles;
    const out = capture(portfolioMock.api, req, url, lens, userId);
    if (!out || out.status !== 200) return false;
    let body = withPods(out.body, roles);
    if (podId) {
      if (!checkPod(podId, projectId, roles, userId, send)) return true;
      body = narrowedWarnings(body, podId);
    }
    send(200, body);
    return true;
  }

  if (p === "/portfolio/scope" && podId) {
    const out = capture(portfolioMock.api, req, url, roles, userId);
    if (!out || out.status !== 200) return false;
    if (!checkPod(podId, projectId, roles, userId, send)) return true;
    const pod = PODS[podId];
    const projects = out.body.projects
      .filter((project) => project.id === pod.project)
      .map((project) => {
        const added = project.added_keys.filter((key) => podOfKey(key) === podId);
        const without = project.without_label_keys.filter((key) => podOfKey(key) === podId);
        return {
          ...project,
          added_keys: added,
          without_label_keys: without,
          plan: project.plan
            ? {
                ...project.plan,
                planned: Math.ceil(project.plan.planned / 2),
                added: added.length,
                without_label: without.length,
                without_label_percent: added.length
                  ? Math.round((without.length / added.length) * 100)
                  : null,
                removed: 0,
              }
            : null,
        };
      });
    const findings = out.body.findings.filter((finding) => podOfKey(finding.issue_key) === podId);
    send(200, { ...out.body, projects, findings, findings_total: findings.length });
    return true;
  }

  if ((m = p.match(/^\/projects\/([^/]+)\/requirements$/)) && podId) {
    const out = capture(reportsLane.api, req, url, roles, userId);
    if (!out || out.status !== 200) return false;
    if (!checkPod(podId, m[1], roles, userId, send)) return true;
    const requirements = out.body.requirements.filter((item) => podOfKey(item.key) === podId);
    const counts = {};
    for (const item of requirements) counts[item.stage] = (counts[item.stage] ?? 0) + 1;
    const done = counts.production ?? 0;
    send(200, {
      ...out.body,
      pod_id: podId,
      pod_name: PODS[podId].name,
      requirements,
      total: requirements.length,
      done,
      percent_complete: requirements.length ? (done / requirements.length) * 100 : null,
      stages: out.body.stages.map((stage) => ({
        ...stage,
        count: counts[stage.stage] ?? 0,
        change: null,
      })),
      // The mock keeps no pod's history: today's split stands alone.
      timeline: out.body.timeline.slice(-1).map((point) => ({
        ...point,
        counts: Object.fromEntries(
          Object.keys(point.counts).map((stage) => [stage, counts[stage] ?? 0]),
        ),
      })),
      moves: out.body.moves.filter((move) => podOfKey(move.key) === podId),
    });
    return true;
  }

  if ((m = p.match(/^\/projects\/([^/]+)\/gates$/)) && podId) {
    const out = capture(reportsLane.api, req, url, roles, userId);
    if (!out || out.status !== 200) return false;
    if (!checkPod(podId, m[1], roles, userId, send)) return true;
    const issues = out.body.issues.filter((issue) => podOfKey(issue.key) === podId);
    const keys = new Set(issues.map((issue) => issue.key));
    send(200, {
      ...out.body,
      pod_id: podId,
      issues,
      questions: out.body.questions.filter((question) => keys.has(question.issue_key)),
    });
    return true;
  }

  if ((m = p.match(/^\/pods\/([^/]+)\/readiness$/)) && req.method === "GET") {
    const pod = PODS[m[1]];
    if (!pod) return refuse(send, 404, `No pod '${m[1]}'.`);
    const ownRun = has(roles, "sm") && OWN_POD[userId] === m[1];
    if (!has(roles, "mgr", "admin") && !ownRun && !(has(roles, "sm") && userId === "U1006")) {
      return refuse(send, 403, "You act on the pods you run and the projects they work on.");
    }
    const asked = new URL(`/projects/${pod.project}/readiness`, url);
    const out = capture(releaseReadiness.api, { ...req, method: "GET" }, asked, roles, userId);
    if (!out || out.status !== 200) return false;
    const findings = out.body.findings.filter(
      (finding) => finding.scope?.kind === "pod" && finding.scope?.id === m[1],
    );
    send(200, {
      ...out.body,
      pod_id: m[1],
      scope_name: pod.name,
      releases: [],
      findings,
    });
    return true;
  }
  return false;
}
