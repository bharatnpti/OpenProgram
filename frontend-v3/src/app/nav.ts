// The sidebar (D85): four groups, each page named by the question it answers.
// Built only from the access map, never from the data, so it is the same length
// and order for everyone with the same role, and never grows with the number of
// projects (D86). Type imports, and runtime imports by their .ts path, so
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
        to: "/portfolio",
        page: "portfolio",
        label: "Will we make the date?",
        hint: "Every project's dates, worst first",
      },
      {
        to: "/projects",
        page: "projects",
        label: "Projects",
        hint: "Everything about one project",
      },
      { to: "/pods", page: "pods", label: "Pods", hint: "One team's day" },
      {
        to: "/warnings",
        page: "warnings",
        label: "Early warnings",
        hint: "What Jira and Git say before people do",
      },
      {
        to: "/scope",
        page: "scope",
        label: "Scope control",
        hint: "What joined after planning, and Jira hygiene",
      },
    ],
  },
  {
    id: "insight",
    label: "Insight",
    items: [
      { to: "/reports", page: "reports", label: "Reports", hint: "Day reports and briefs" },
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
export function shownGroups(access: Pick<Access, "pages" | "reports">): NavGroup[] {
  return NAV_GROUPS.map((group) => ({
    ...group,
    items: group.items.filter((item) => access.pages[item.page]).map((item) => shown(item, access)),
  })).filter((group) => group.items.length > 0);
}

/** An item as this role reads it: Reports without day reports is the briefs (D103). */
function shown(item: NavItem, access: Pick<Access, "reports">): NavItem {
  if (item.page !== "reports" || access.reports.daily) return item;
  return { ...item, to: "/reports/briefs", hint: "Briefs written from the day's facts" };
}

/** The destinations this role is offered, flat: the palette's screens. */
export function shownNav(access: Pick<Access, "pages" | "reports">): NavItem[] {
  return shownGroups(access).flatMap((group) => group.items);
}
