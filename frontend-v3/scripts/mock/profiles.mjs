// Mock for profiles per pod membership (lane B): the catalog, a pod's people with their role and
// profile, one membership's suggestion, the review list with Accept all, the member's own
// profiles, and the profile an admin sends when adding someone to a pod or importing people into
// one. NOT real data: shapes follow src/api/generated.ts, the people are the demo roster in
// mock-console.mjs, and the suggestion is a small copy of the backend's title rule.
//
// Who is in a pod is the structure mock's (admin-structure.mjs, which keeps the directory lists'
// member_ids current). Adding someone with a profile, or importing into a pod, is handed to it
// with the body replayed, and the profile is recorded once it answers 2xx.
//
// Wired in by scripts/mock-api.mjs with one import and one call. It returns true when it handled
// the request; a write answers once its body has been read.
import { Readable } from "node:stream";

import * as consoleData from "../mock-console.mjs";
import * as adminStructure from "./admin-structure.mjs";

const TODAY = new Date().toISOString().slice(0, 10);
const detail = (message) => ({ detail: message });
const dec = decodeURIComponent;

// ---- The catalog ---------------------------------------------------------------------------

const DEFAULTS = [
  ["development", "Development", true, ["engineer", "developer"], 10],
  ["qa", "QA and test", false, ["qa", "test", "tester", "quality"], 20],
  ["security", "Security", false, ["security", "appsec"], 30],
  ["product", "Product", false, ["product owner", "product", "business analyst"], 40],
  ["design", "UX and design", false, ["designer", "design", "ux"], 50],
  ["delivery", "TPM / delivery", false, ["scrum master", "program manager", "tpm"], 60],
  ["ops", "Ops / SRE / platform", true, ["sre", "devops", "platform"], 70],
  ["data", "Data and analytics", true, ["data", "analyst"], 80],
  ["architecture", "Architecture", false, ["architect"], 90],
  ["management", "Engineering management", false, ["manager", "director", "head of"], 100],
  ["other", "Other", false, [], 110],
].map(([slug, label, code_work, title_words, sort]) => ({
  slug,
  label,
  code_work,
  title_words,
  sort,
  hidden: false,
  customized: false,
}));
const overrides = new Map();
const catalog = () => {
  const merged = new Map(DEFAULTS.map((entry) => [entry.slug, entry]));
  for (const [slug, entry] of overrides) merged.set(slug, entry);
  return [...merged.values()].sort((a, b) => a.sort - b.sort || a.label.localeCompare(b.label));
};
const labelOf = (slug) => catalog().find((entry) => entry.slug === slug)?.label ?? slug;
const ROLE_PROFILE = {
  developer: "development",
  product_owner: "product",
  scrum_master: "delivery",
  manager: "management",
};

// ---- Memberships and their rows -------------------------------------------------------------

const personOf = (id) => consoleData.roster.find((person) => person.id === id);
const nameOf = (id) => personOf(id)?.name ?? id;
const podOf = (id) => consoleData.pods.find((pod) => pod.id === id);
const roleOf = (id) => {
  const roles = personOf(id)?.roles ?? [];
  if (roles.includes("sm")) return "scrum_master";
  if (roles.includes("po")) return "product_owner";
  if (roles.includes("mgr")) return "manager";
  return "developer";
};
/** The people in a pod: the directory list's, which the structure mock keeps current. */
const membersOf = (podId) => podOf(podId)?.member_ids ?? [];
const runs = (podId, userId) =>
  (personOf(userId)?.pods ?? []).includes(podId) ||
  podOf(podId)?.metadata?.escalation_sm_member_id === userId;

/** Rows by "pod:member": one demo profile someone already set, the rest from the pod role. */
const rows = new Map([
  [
    "pod-payments:U1009",
    { profile: "qa", source: "admin", since: "2026-10-01", set_by_name: "Asha Rao" },
  ],
]);
const roles = new Map();

function view(podId, memberId) {
  const row = rows.get(`${podId}:${memberId}`);
  const role = roles.get(`${podId}:${memberId}`) ?? roleOf(memberId);
  const profile = row?.profile ?? ROLE_PROFILE[role] ?? "development";
  const entry = catalog().find((item) => item.slug === profile);
  return {
    pod_id: podId,
    pod_name: podOf(podId)?.name ?? podId,
    member_id: memberId,
    member_name: nameOf(memberId),
    role,
    profile,
    label: entry?.label ?? profile,
    code_work: entry?.code_work ?? false,
    source: row?.source ?? "pod_role",
    checked: row ? row.source !== "backfill" : false,
    since: row?.since ?? null,
    set_by_name: row?.set_by_name ?? null,
    set_at: null,
    is_lead: false,
  };
}

function suggestion(memberId, role) {
  const title = (personOf(memberId)?.title ?? "").toLowerCase();
  const words = title.split(/[^a-z0-9]+/).filter(Boolean);
  let best = null;
  for (const entry of catalog().filter((item) => !item.hidden)) {
    for (const phrase of entry.title_words) {
      const needle = phrase.split(" ");
      const at = words.findIndex((_, i) => needle.every((word, k) => words[i + k] === word));
      if (at < 0) continue;
      const rank = [-needle.length, at];
      if (!best || rank[0] < best.rank[0] || (rank[0] === best.rank[0] && rank[1] < best.rank[1]))
        best = { rank, slug: entry.slug };
    }
  }
  if (best) {
    return {
      profile: best.slug,
      label: labelOf(best.slug),
      reasons: [`their title says '${personOf(memberId).title}'`],
    };
  }
  const slug = ROLE_PROFILE[role] ?? "development";
  return {
    profile: slug,
    label: labelOf(slug),
    reasons: [`from their role in the pod (${String(role).replace(/_/g, " ")})`],
  };
}

const canSet = (podId, roleList, userId) =>
  roleList.includes("admin") || (roleList.includes("sm") && runs(podId, userId));
const NOT_A_SETTER = "Only an admin, or a scrum master in a pod they run, sets profiles.";
const NOT_YOUR_POD = "You set profiles in the pods you run, and this is not one of them.";

function record(podId, memberId, profile, source, userId) {
  rows.set(`${podId}:${memberId}`, {
    profile,
    source,
    since: TODAY,
    set_by_name: nameOf(userId),
  });
}

function offered(profile) {
  const entry = catalog().find((item) => item.slug === profile);
  if (!entry) return `There is no profile '${profile}'. Pick one from the list.`;
  if (entry.hidden) return `The profile '${entry.label}' is hidden. Pick another one.`;
  return null;
}

// ---- Bodies ----------------------------------------------------------------------------------

const readRaw = (req) =>
  new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => {
      raw += chunk;
    });
    req.on("end", () => resolve(raw));
  });
const parse = (raw) => {
  try {
    return raw ? JSON.parse(raw) : {};
  } catch {
    return null;
  }
};
/** The request again with the same body, for the structure mock to read. */
const replay = (req, raw, method = req.method) => {
  const copy = Readable.from(raw ? [raw] : []);
  copy.method = method;
  copy.headers = req.headers;
  copy.url = req.url;
  return copy;
};
/** Hands a request to the structure mock and resolves with what it answered. */
const viaStructure = (req, raw, url, roleList, userId, deny) =>
  new Promise((resolve) => {
    const handled = adminStructure.api(
      replay(req, raw),
      url,
      roleList,
      userId,
      (status, payload) => resolve([status, payload]),
      () => resolve([403, detail(`Role ${roleList.join(",") || "none"} lacks this capability.`)]),
    );
    if (handled === false) resolve(null);
    void deny;
  });

// ---- The routes ----------------------------------------------------------------------------------

/** Answers a profile endpoint, or returns false when the path is not one of these. */
export function api(req, url, roleList, userId, send, deny) {
  const p = url.pathname;
  const method = req.method ?? "GET";
  const who = userId ?? "U1001";
  let m;

  if (p === "/profiles" && method === "GET") {
    send(200, catalog());
    return true;
  }
  if ((m = p.match(/^\/config\/profiles\/([^/]+)$/)) && method === "PUT") {
    if (!roleList.includes("admin")) {
      deny();
      return true;
    }
    const slug = dec(m[1]);
    void readRaw(req).then((raw) => {
      const body = parse(raw);
      if (!body) return send(422, detail("The request body is not valid JSON."));
      if (!/^[a-z][a-z0-9_]{0,31}$/.test(slug))
        return send(
          422,
          detail("A profile's id is 1 to 32 lower-case letters, digits or underscores."),
        );
      const entry = {
        slug,
        label: String(body.label ?? "").trim() || slug,
        code_work: Boolean(body.code_work),
        title_words: body.title_words ?? [],
        sort: body.sort ?? 100,
        hidden: Boolean(body.hidden),
        customized: true,
      };
      overrides.set(slug, entry);
      return send(200, entry);
    });
    return true;
  }
  if ((m = p.match(/^\/pods\/([^/]+)\/profiles$/)) && method === "GET") {
    const podId = dec(m[1]);
    if (!podOf(podId)) {
      send(404, detail(`No pod '${podId}'.`));
      return true;
    }
    const reads =
      roleList.some((role) => ["sm", "mgr", "admin"].includes(role)) ||
      (roleList.includes("dev") && membersOf(podId).includes(who));
    if (!reads) {
      send(403, detail("You read your own pod's details, and this is not your pod."));
      return true;
    }
    send(200, {
      pod_id: podId,
      pod_name: podOf(podId).name,
      as_of: TODAY,
      can_edit: canSet(podId, roleList, who),
      members: membersOf(podId)
        .map((id) => view(podId, id))
        .sort((a, b) => a.member_name.localeCompare(b.member_name)),
    });
    return true;
  }
  if (p === "/me/profiles" && method === "GET") {
    send(
      200,
      consoleData.pods
        .filter((pod) => membersOf(pod.id).includes(who))
        .map((pod) => view(pod.id, who)),
    );
    return true;
  }
  if ((m = p.match(/^\/config\/pods\/([^/]+)\/members\/([^/]+)\/profile$/))) {
    const [podId, memberId] = [dec(m[1]), dec(m[2])];
    if (!canSet(podId, roleList, who)) {
      send(403, detail(roleList.includes("sm") ? NOT_YOUR_POD : NOT_A_SETTER));
      return true;
    }
    if (!podOf(podId) || !personOf(memberId)) {
      send(404, detail(`No pod or member '${podId}', '${memberId}'.`));
      return true;
    }
    const inPod = membersOf(podId).includes(memberId);
    if (method === "GET") {
      const role =
        url.searchParams.get("role") ?? (inPod ? view(podId, memberId).role : "developer");
      send(200, {
        pod_id: podId,
        pod_name: podOf(podId).name,
        member_id: memberId,
        member_name: nameOf(memberId),
        current: inPod ? view(podId, memberId) : null,
        history: [],
        suggestion: suggestion(memberId, role),
      });
      return true;
    }
    if (method === "PUT") {
      void readRaw(req).then((raw) => {
        const body = parse(raw) ?? {};
        if (!inPod) return send(404, detail(`${memberId} is not in ${podId}; add them first.`));
        const refused = offered(body.profile);
        if (refused) return send(422, detail(refused));
        record(
          podId,
          memberId,
          body.profile,
          body.accepted_suggestion ? "suggestion_accepted" : "admin",
          who,
        );
        return send(200, view(podId, memberId));
      });
      return true;
    }
  }
  if (p === "/config/profiles/review" && method === "GET") {
    if (!roleList.some((role) => ["sm", "admin"].includes(role))) {
      send(403, detail(NOT_A_SETTER));
      return true;
    }
    const only = url.searchParams.get("pod_id");
    const items = consoleData.pods
      .filter((pod) => (only ? pod.id === only : true) && canSet(pod.id, roleList, who))
      .flatMap((pod) => membersOf(pod.id).map((id) => view(pod.id, id)))
      .filter((current) => !current.checked)
      .map((current) => ({ current, suggestion: suggestion(current.member_id, current.role) }));
    send(200, { items });
    return true;
  }
  if (p === "/config/profiles/review/accept" && method === "POST") {
    void readRaw(req).then((raw) => {
      const body = parse(raw) ?? {};
      const items = body.items ?? [];
      const refused = items.find((item) => !canSet(item.pod_id, roleList, who));
      if (refused) return send(403, detail(roleList.includes("sm") ? NOT_YOUR_POD : NOT_A_SETTER));
      for (const item of items)
        record(item.pod_id, item.member_id, item.profile, "suggestion_accepted", who);
      return send(200, { accepted: items.map((item) => view(item.pod_id, item.member_id)) });
    });
    return true;
  }

  // Adding someone with a profile: the structure mock links them, then the profile is kept.
  if ((m = p.match(/^\/config\/pods\/([^/]+)\/members\/([^/]+)$/)) && method === "POST") {
    const [podId, memberId] = [dec(m[1]), dec(m[2])];
    void readRaw(req).then(async (raw) => {
      const body = parse(raw) ?? {};
      if (body.profile) {
        const refused = offered(body.profile);
        if (refused) return send(422, detail(refused));
      }
      const answered = await viaStructure(req, raw, url, roleList, who, deny);
      if (!answered) return send(404, detail("Not found."));
      const [status, payload] = answered;
      if (status < 300) {
        roles.set(`${podId}:${memberId}`, body.role);
        if (body.profile) record(podId, memberId, body.profile, "admin", who);
      }
      return send(status, payload);
    });
    return true;
  }
  // Importing into a pod: import, then add each to the pod, then keep the profile.
  if (p === "/config/members/from-directory" && method === "POST") {
    void readRaw(req).then(async (raw) => {
      const body = parse(raw) ?? {};
      if (!body.pod_id) {
        const answered = await viaStructure(req, raw, url, roleList, who, deny);
        return answered ? send(...answered) : send(404, detail("Not found."));
      }
      if (!podOf(body.pod_id)) return send(404, detail(`No pod '${body.pod_id}'.`));
      if (body.profile) {
        const refused = offered(body.profile);
        if (refused) return send(422, detail(refused));
      }
      const imported = await viaStructure(req, raw, url, roleList, who, deny);
      if (!imported || imported[0] >= 300)
        return send(...(imported ?? [404, detail("Not found.")]));
      const role = body.role ?? "developer";
      for (const member of imported[1]) {
        const link = new URL(`/config/pods/${body.pod_id}/members/${member.id}`, url);
        const joined = await viaStructure(
          replay(req, "", "POST"),
          JSON.stringify({ role }),
          link,
          roleList,
          who,
          deny,
        );
        if (joined && joined[0] < 300) roles.set(`${body.pod_id}:${member.id}`, role);
        if (body.profile) record(body.pod_id, member.id, body.profile, "import", who);
      }
      return send(imported[0], imported[1]);
    });
    return true;
  }
  return false;
}
