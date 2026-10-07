import { useSearchParams } from "react-router-dom";

import { useRole } from "../app/role";
import { PanelState, SectionHeader } from "../components/PanelState";
import { BrandingTab } from "../features/admin/BrandingTab";
import { CheckinsTab } from "../features/admin/CheckinsTab";
import { ContactsTab } from "../features/admin/ContactsTab";
import { DataSourcesTab } from "../features/admin/DataSourcesTab";
import { DeliveryStagesTab } from "../features/admin/DeliveryStagesTab";
import { DirectoryTab } from "../features/admin/DirectoryTab";
import { EntitiesTab } from "../features/admin/EntitiesTab";
import { EscalationTab } from "../features/admin/EscalationTab";
import { GatesTab } from "../features/admin/GatesTab";
import { IntegrationsTab } from "../features/admin/IntegrationsTab";
import { LinksTab } from "../features/admin/LinksTab";
import { cn } from "../lib/utils";

const CONSOLE_URL = import.meta.env.VITE_CONSOLE_URL ?? "http://127.0.0.1:5174";

// One entry per line, so each lane's tabs merge as separate lines.
const TABS = [
  { key: "checkins", label: "Check-ins" },
  { key: "sources", label: "Data sources" },
  { key: "integrations", label: "Integrations" },
  { key: "stages", label: "Delivery stages" },
  { key: "gates", label: "Gates" },
  { key: "escalation", label: "Escalation" },
  { key: "branding", label: "Branding" },
  { key: "entities", label: "Entities" },
  { key: "links", label: "Links" },
  { key: "directory", label: "Directory" },
  { key: "contacts", label: "Escalation contacts" },
  { key: "more", label: "More settings" },
] as const;
type Tab = (typeof TABS)[number]["key"];

/** Runtime configuration. Always shows current configuration, whatever day is being viewed. */
export function AdminPage() {
  const { canManageConfig } = useRole();
  const [search, setSearch] = useSearchParams();
  const tab: Tab = TABS.some((t) => t.key === search.get("tab"))
    ? (search.get("tab") as Tab)
    : "checkins";

  return (
    <>
      <SectionHeader
        title="Admin"
        meta="Check-ins, where data comes from and goes to, how delivery is counted and escalated, and the hierarchy."
      />
      <PanelState locked={!canManageConfig} needs="an admin" isLoading={false} error={null}>
        <nav
          aria-label="Admin sections"
          className="mb-6 flex gap-1 overflow-x-auto border-b border-grey-border"
        >
          {TABS.map((t) => (
            <button
              key={t.key}
              type="button"
              aria-pressed={t.key === tab}
              onClick={() => setSearch({ tab: t.key }, { replace: true })}
              className={cn(
                "relative flex-none px-3 pb-3 pt-2 text-[14px] font-bold",
                t.key === tab
                  ? "text-ink after:absolute after:inset-x-3 after:bottom-0 after:h-[3px] after:rounded-full after:bg-magenta"
                  : "text-grey-body hover:text-ink",
              )}
            >
              {t.label}
            </button>
          ))}
        </nav>
        {tab === "checkins" ? <CheckinsTab /> : null}
        {tab === "sources" ? <DataSourcesTab /> : null}
        {tab === "integrations" ? <IntegrationsTab /> : null}
        {tab === "stages" ? <DeliveryStagesTab /> : null}
        {tab === "gates" ? <GatesTab /> : null}
        {tab === "escalation" ? <EscalationTab /> : null}
        {tab === "branding" ? <BrandingTab /> : null}
        {tab === "entities" ? <EntitiesTab /> : null}
        {tab === "links" ? <LinksTab /> : null}
        {tab === "directory" ? <DirectoryTab /> : null}
        {tab === "contacts" ? <ContactsTab /> : null}
        {tab === "more" ? <MoreSettings /> : null}
      </PanelState>
    </>
  );
}

const MORE = [
  [
    "Links",
    "Which projects sit in which program, pods in projects and workstreams, members in pods, tasks to people.",
  ],
  [
    "Directory",
    "Sync people from the chat directory, import them as members, and set identity links to chat, Jira and Git.",
  ],
];

function MoreSettings() {
  return (
    <div className="grid grid-cols-[minmax(0,1fr)] gap-3 md:grid-cols-2">
      {MORE.map(([title, text]) => (
        <a
          key={title}
          href={`${CONSOLE_URL}/admin`}
          className="rounded-3xl border border-grey-border p-5 text-ink no-underline hover:shadow-op-hover"
        >
          <p className="text-[16px] font-extrabold">{title}</p>
          <p className="mt-1 text-[13px] text-grey-body">{text}</p>
          <p className="mt-2 text-[12px] font-bold text-magenta">Set in the console →</p>
        </a>
      ))}
    </div>
  );
}
