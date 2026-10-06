import type { AppRole } from "./role";

export type NavItem = { to: string; label: string; offered: (role: Roles) => boolean };
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
  { to: "/today", label: "Today", offered: () => true },
  { to: "/delivery", label: "Delivery", offered: () => true },
  { to: "/signals", label: "Signals", offered: (r) => r.canReadAggregate },
  { to: "/coordination", label: "Coordination", offered: () => true },
  { to: "/reports", label: "Reports", offered: () => true },
  { to: "/chat", label: "Chat", offered: (r) => r.chatEnabled },
  { to: "/admin", label: "Admin", offered: (r) => r.canManageConfig },
];
