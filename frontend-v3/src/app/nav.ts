// The sidebar: four groups, each page named by the question it answers.
// Built only from the access map, never from the data, so it is the same length
// and order for everyone with the same role, and never grows with the number of
// projects. Type imports, and runtime imports by their .ts path, so
// `node --test` runs it as written.
import { ASK_LABEL } from "../features/assistant/persona.ts";
import type { Access, Page } from "./access";

export type NavItem = {
  to: string;
  page: Page;
  label: string;
  /** What is there, in a few words, for the ⌘K palette. */
  hint: string;
};

export type NavGroupId = "daily" | "delivery" | "insight" | "setup";

export type NavGroup = { id: NavGroupId; label: string; items: NavItem[] };

export const NAV_GROUPS: NavGroup[] = [
  {
    id: "daily",
    label: "Daily",
    items: [
      { to: "/today", page: "today", label: "Today", hint: "What needs me today" },
      { to: "/check-in", page: "checkin", label: "Check-in", hint: "Where my work stands" },
      { to: "/requests", page: "requests", label: "Requests", hint: "Who is waiting on whom" },
    ],
  },
  {
    id: "delivery",
    label: "Delivery",
    items: [
      {
        to: "/projects",
        page: "projects",
        label: "Projects",
        hint: "Dates, warnings, scope and reports, for all projects, one or a pod",
      },
    ],
  },
  {
    id: "insight",
    label: "Insight",
    items: [
      { to: "/flow", page: "flow", label: "Review flow", hint: "Where work waits in review" },
      { to: "/ora", page: "ora", label: ASK_LABEL, hint: "Ask anything in plain words" },
    ],
  },
  {
    id: "setup",
    label: "Setup",
    items: [
      {
        to: "/people",
        page: "people",
        label: "People & roles",
        hint: "Who is in which pod, with which profile",
      },
      { to: "/admin", page: "admin", label: "Admin", hint: "Configuration" },
    ],
  },
];

/** Every destination, in the sidebar's order. */
export const NAV: NavItem[] = NAV_GROUPS.flatMap((group) => group.items);

/**
 * The groups this role is offered, each with only its offered pages; a group
 * with none is left out. No struck-through item for the rest.
 */
export function shownGroups(access: Pick<Access, "pages">): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => access.pages[item.page]),
  })).filter((group) => group.items.length > 0);
}

/** The destinations this role is offered, flat: the palette's screens. */
export function shownNav(access: Pick<Access, "pages">): NavItem[] {
  return shownGroups(access).flatMap((group) => group.items);
}
