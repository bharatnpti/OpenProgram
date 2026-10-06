// Who a pod's waiting asks reach: the scrum master and the manager. Picking, wording and the
// request, as pure logic. Type imports only, so `node --test` can run it.
//
// The backend replaces both contacts on every save: a contact left out of the request is
// cleared. So the form always sends both, and a contact saved before members could be picked
// (a bare chat id that matches nobody) is kept by sending that id back as it is.
import type {
  EscalationCandidateResponse,
  EscalationContactDto,
  EscalationContactUpdateDto,
  PodEscalationContactsResponse,
  PodEscalationContactsUpdateRequest,
} from "../../api/schema";
import { podRoleLabel } from "./structure.ts";

/** The picker value for a saved contact whose chat id matches no member: keep it as it is. */
export const KEEP_SAVED = "__saved__";

/** The two rungs above the person, with the pod role that fits each. */
export const CONTACT_SLOTS = [
  { field: "scrum_master", label: "Scrum master", podRole: "scrum_master" },
  { field: "manager", label: "Manager", podRole: "manager" },
] as const;

export type ContactField = (typeof CONTACT_SLOTS)[number]["field"];

export type Picks = Record<ContactField, string>;

/** What a picker shows for a saved contact: its member, else "keep what is saved", else nobody. */
export function initialPick(contact: EscalationContactDto | null | undefined): string {
  if (!contact) return "";
  return contact.member_id ?? KEEP_SAVED;
}

export function initialPicks(contacts: PodEscalationContactsResponse | undefined): Picks {
  return {
    scrum_master: initialPick(contacts?.scrum_master),
    manager: initialPick(contacts?.manager),
  };
}

/** The request for one slot: a member, the saved chat id kept as it is, or null to clear it. */
export function contactChoice(
  pick: string,
  saved: EscalationContactDto | null | undefined,
): EscalationContactUpdateDto | null {
  if (!pick) return null;
  if (pick === KEEP_SAVED) return saved ? { chat_external_id: saved.chat_external_id } : null;
  return { member_id: pick };
}

export function buildContactsUpdate(
  picks: Picks,
  saved: PodEscalationContactsResponse | undefined,
): PodEscalationContactsUpdateRequest {
  return {
    scrum_master: contactChoice(picks.scrum_master, saved?.scrum_master),
    manager: contactChoice(picks.manager, saved?.manager),
  };
}

/** Whether saving would change anything, so the Save button can stay off until it does. */
export function picksChanged(
  picks: Picks,
  saved: PodEscalationContactsResponse | undefined,
): boolean {
  const before = initialPicks(saved);
  return picks.scrum_master !== before.scrum_master || picks.manager !== before.manager;
}

/** "Ira Novak · Scrum master", with why a person cannot be picked. */
export function candidateLabel(candidate: EscalationCandidateResponse): string {
  const parts = [candidate.name];
  if (candidate.in_pod && candidate.pod_role) parts.push(podRoleLabel(candidate.pod_role));
  if (!candidate.chat_user_id) parts.push("no chat id yet");
  return parts.join(" · ");
}

/** Pod members first, those with the matching role at the top, then everyone else. */
export function groupCandidates(
  candidates: readonly EscalationCandidateResponse[],
  podRole: string,
): { inPod: EscalationCandidateResponse[]; others: EscalationCandidateResponse[] } {
  const inPod = candidates
    .filter((candidate) => candidate.in_pod)
    .sort((a, b) => Number(a.pod_role !== podRole) - Number(b.pod_role !== podRole));
  return { inPod, others: candidates.filter((candidate) => !candidate.in_pod) };
}

/** A saved contact as a person reads it: the name, else the chat id it was saved with. */
export function contactName(contact: EscalationContactDto | null | undefined): string | null {
  if (!contact) return null;
  return contact.display_name ?? contact.chat_external_id;
}

export type ContactState = { tone: "success" | "warning" | "neutral"; label: string };

/** Whether a pod's contacts would reach anyone, in one chip. */
export function contactState(
  contacts: Pick<PodEscalationContactsResponse, "scrum_master" | "manager"> | undefined,
): ContactState {
  const sm = contacts?.scrum_master;
  const manager = contacts?.manager;
  if (!sm && !manager) return { tone: "warning", label: "Nobody above the person is told" };
  if (sm && manager) return { tone: "success", label: "Both set" };
  return { tone: "warning", label: sm ? "Scrum master only" : "Manager only" };
}
