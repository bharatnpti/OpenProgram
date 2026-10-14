// Admin's tabs, grouped in five. The `?tab=` keys stay as
// they were, so every link into Admin holds. No imports, so `node --test` runs it.

export const ADMIN_GROUPS = [
  {
    label: "Check-ins",
    tabs: [
      { key: "checkins", label: "Check-ins" },
      { key: "escalation", label: "Escalation" },
      { key: "contacts", label: "Escalation contacts" },
    ],
  },
  {
    label: "Delivery rules",
    tabs: [
      { key: "stages", label: "Delivery stages" },
      { key: "gates", label: "Gates" },
      { key: "readiness", label: "Release readiness" },
      { key: "hygiene", label: "Jira hygiene" },
    ],
  },
  {
    label: "Connections",
    tabs: [
      { key: "sources", label: "Data sources" },
      { key: "integrations", label: "Integrations" },
      { key: "jira-writes", label: "Jira writes" },
    ],
  },
  {
    label: "Organisation",
    tabs: [
      { key: "entities", label: "Entities" },
      { key: "links", label: "Links" },
      { key: "directory", label: "Directory" },
      { key: "profiles", label: "Profiles" },
    ],
  },
  { label: "Look", tabs: [{ key: "branding", label: "Branding" }] },
] as const;

export type AdminTab = (typeof ADMIN_GROUPS)[number]["tabs"][number]["key"];

export const ADMIN_TABS: { key: AdminTab; label: string }[] = ADMIN_GROUPS.flatMap((group) => [
  ...group.tabs,
]);

/** The tab a link asks for, else Check-ins. */
export function adminTab(asked: string | null): AdminTab {
  return ADMIN_TABS.find((tab) => tab.key === asked)?.key ?? "checkins";
}
