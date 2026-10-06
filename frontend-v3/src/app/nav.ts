import type { AppRole } from "./roleWords";

export type NavItem = {
  to: string;
  label: string;
  /** What is there, in a few words, for the ⌘K palette. */
  hint: string;
  offered: (role: Roles) => boolean;
};
type Roles = {
  role: AppRole;
  canReadAggregate: boolean;
  canManageConfig: boolean;
  chatEnabled: boolean;
};

/**
 * The seven destinations, in the design's order. A destination the viewing
 * role is not offered stays visible but struck through, so people can see the
 * shape of the product and what another role would open. Chat only exists on
 * a tenant that serves the built-in chat.
 */
export const NAV: NavItem[] = [
  { to: "/today", label: "Today", hint: "Your day", offered: () => true },
  {
    to: "/delivery",
    label: "Delivery",
    hint: "Programs, projects, workstreams and pods",
    offered: () => true,
  },
  {
    to: "/signals",
    label: "Signals",
    hint: "Risks, drift and flow",
    offered: (r) => r.canReadAggregate,
  },
  { to: "/coordination", label: "Coordination", hint: "Requests and briefs", offered: () => true },
  { to: "/reports", label: "Reports", hint: "Daily and Overall", offered: () => true },
  {
    to: "/chat",
    label: "Chat",
    hint: "The check-in conversation",
    offered: (r) => r.chatEnabled,
  },
  { to: "/admin", label: "Admin", hint: "Configuration", offered: (r) => r.canManageConfig },
];

/** The destinations shown in the navigation: all of them, except Chat where it isn't served. */
export function shownNav(roles: Roles): NavItem[] {
  return NAV.filter((item) => item.to !== "/chat" || roles.chatEnabled);
}

/** The destinations this role may open, for the palette. */
export function offeredNav(roles: Roles): NavItem[] {
  return NAV.filter((item) => item.offered(roles));
}
