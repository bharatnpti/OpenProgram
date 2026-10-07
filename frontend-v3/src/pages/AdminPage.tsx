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

// Every setting has its own tab here, so nothing links out to the frontend-v2
// console any more. Escalation contacts sit beside Escalation: the matrix's
// team scrum master and team manager levels are those contacts.
const TABS = [
  { key: "checkins", label: "Check-ins" },
  { key: "sources", label: "Data sources" },
  { key: "integrations", label: "Integrations" },
  { key: "stages", label: "Delivery stages" },
  { key: "gates", label: "Gates" },
  { key: "escalation", label: "Escalation" },
  { key: "contacts", label: "Escalation contacts" },
  { key: "branding", label: "Branding" },
  { key: "entities", label: "Entities" },
  { key: "links", label: "Links" },
  { key: "directory", label: "Directory" },
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
        {tab === "contacts" ? <ContactsTab /> : null}
        {tab === "branding" ? <BrandingTab /> : null}
        {tab === "entities" ? <EntitiesTab /> : null}
        {tab === "links" ? <LinksTab /> : null}
        {tab === "directory" ? <DirectoryTab /> : null}
      </PanelState>
    </>
  );
}
