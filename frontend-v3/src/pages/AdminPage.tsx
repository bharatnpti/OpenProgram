import { useSearchParams } from "react-router-dom";

import { SectionHeader } from "../components/PanelState";
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
import { JiraHygieneTab } from "../features/admin/JiraHygieneTab";
import { JiraWritesTab } from "../features/admin/JiraWritesTab";
import { LinksTab } from "../features/admin/LinksTab";
import { ProfilesTab } from "../features/admin/ProfilesTab";
import { ReleaseReadinessTab } from "../features/admin/ReleaseReadinessTab";
import { ADMIN_GROUPS, adminTab, type AdminTab } from "../features/admin/adminTabs";
import { cn } from "../lib/utils";

/**
 * Runtime configuration, every setting in its own tab, the tabs grouped in five
 * (features/admin/adminTabs.ts): a left list on a wide screen, a grouped picker
 * on a narrow one. Escalation contacts sit beside Escalation: the matrix's team
 * scrum master and team manager levels are those contacts. Always shows the
 * current configuration, whatever day is being viewed.
 */
export function AdminPage() {
  const [search, setSearch] = useSearchParams();
  const tab = adminTab(search.get("tab"));
  const pick = (key: AdminTab) => setSearch({ tab: key }, { replace: true });

  return (
    <>
      <SectionHeader
        title="Admin"
        meta="Check-ins, where data comes from and goes to, what may be written to Jira, how delivery is counted and escalated, and the hierarchy."
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-6 lg:grid-cols-[200px_minmax(0,1fr)]">
        <nav aria-label="Admin sections" className="max-lg:hidden">
          {ADMIN_GROUPS.map((group) => (
            <div key={group.label} className="mb-4 last:mb-0">
              <p className="px-3 pb-1 text-[11px] font-bold uppercase tracking-wider text-grey-secondary">
                {group.label}
              </p>
              <ul className="grid gap-0.5">
                {group.tabs.map((t) => (
                  <li key={t.key}>
                    <button
                      type="button"
                      aria-pressed={t.key === tab}
                      onClick={() => pick(t.key)}
                      className={cn(
                        "w-full rounded-xl px-3 py-2 text-left text-[14px] font-bold",
                        t.key === tab ? "bg-ink text-white" : "text-grey-body hover:bg-grey-fill",
                      )}
                    >
                      {t.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </nav>
        <div className="lg:hidden">
          <label htmlFor="admin-section" className="sr-only">
            Admin section
          </label>
          <select
            id="admin-section"
            value={tab}
            onChange={(event) => pick(adminTab(event.target.value))}
            className="h-10 w-full rounded-full border border-grey-border bg-white px-4 text-[14px] font-bold"
          >
            {ADMIN_GROUPS.map((group) => (
              <optgroup key={group.label} label={group.label}>
                {group.tabs.map((t) => (
                  <option key={t.key} value={t.key}>
                    {t.label}
                  </option>
                ))}
              </optgroup>
            ))}
          </select>
        </div>
        <div className="min-w-0">
          {tab === "checkins" ? <CheckinsTab /> : null}
          {tab === "sources" ? <DataSourcesTab /> : null}
          {tab === "integrations" ? <IntegrationsTab /> : null}
          {tab === "jira-writes" ? <JiraWritesTab /> : null}
          {tab === "stages" ? <DeliveryStagesTab /> : null}
          {tab === "gates" ? <GatesTab /> : null}
          {tab === "readiness" ? <ReleaseReadinessTab /> : null}
          {tab === "hygiene" ? <JiraHygieneTab /> : null}
          {tab === "escalation" ? <EscalationTab /> : null}
          {tab === "contacts" ? <ContactsTab /> : null}
          {tab === "branding" ? <BrandingTab /> : null}
          {tab === "entities" ? <EntitiesTab /> : null}
          {tab === "links" ? <LinksTab /> : null}
          {tab === "directory" ? <DirectoryTab /> : null}
          {tab === "profiles" ? <ProfilesTab /> : null}
        </div>
      </div>
    </>
  );
}
