import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { PanelState } from "../../components/PanelState";
import { TabIntro } from "../../features/hub/TabIntro";
import { WarningGroup } from "../../features/warnings/WarningGroups";
import { countLine, shownGroups } from "../../features/warnings/warningWords";
import { formatDay } from "../../lib/format";

/**
 * All projects › Early warnings (`/portfolio/warnings`): what Jira and Git
 * say, whatever anyone reports, grouped by kind with a count each, in one read
 * across the projects the person reads (`GET /portfolio/warnings`). Every
 * warning has an owner; findings attach to work items and nobody is ranked.
 * One project's are on its own Early warnings tab.
 */
export function PortfolioWarnings() {
  const warnings = useQuery({
    queryKey: ["portfolio", "warnings", ""],
    queryFn: () => apiClient.portfolioWarnings({}),
  });
  const view = warnings.data;
  const seenSince = view?.seen_since;
  return (
    <div>
      <TabIntro
        tab="warnings"
        meta={
          <>
            What Jira and Git say, whatever anyone reports. Every warning has an owner; findings
            attach to work items, and nobody is ranked.{view ? ` ${countLine(view)}` : ""}
            {seenSince
              ? ` Ages of disagreements count from ${formatDay(seenSince)}, when they were first recorded.`
              : ""}
          </>
        }
      />
      <PanelState
        isLoading={warnings.isLoading}
        error={warnings.error}
        onRetry={() => void warnings.refetch()}
      >
        {view ? (
          <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
            {shownGroups(view).map((group) => (
              <WarningGroup key={group.kind} group={group} seenSince={seenSince} />
            ))}
          </div>
        ) : null}
      </PanelState>
    </div>
  );
}
