import type { Access, Page } from "./access";

export type NavItem = {
  to: string;
  page: Page;
  label: string;
  /** What is there, in a few words, for the ⌘K palette. */
  hint: string;
};

/**
 * The seven destinations, in the design's order. Each role sees only the ones
 * it is offered (app/access.ts); there is no struck-through tab for the rest.
 */
export const NAV: NavItem[] = [
  { to: "/today", page: "today", label: "Today", hint: "Your day" },
  {
    to: "/delivery",
    page: "delivery",
    label: "Delivery",
    hint: "Programs, projects, workstreams and pods",
  },
  { to: "/signals", page: "signals", label: "Signals", hint: "Flow and risks" },
  {
    to: "/coordination",
    page: "coordination",
    label: "Coordination",
    hint: "Requests, briefs and Ask the graph",
  },
  { to: "/reports", page: "reports", label: "Reports", hint: "Daily and Overall" },
  { to: "/chat", page: "chat", label: "Chat", hint: "The check-in conversation" },
  { to: "/admin", page: "admin", label: "Admin", hint: "Configuration" },
];

/** The destinations this role is offered: the navigation's tabs and the palette's screens. */
export function shownNav(access: Pick<Access, "pages">): NavItem[] {
  return NAV.filter((item) => access.pages[item.page]);
}
