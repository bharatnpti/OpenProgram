import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../api/client";
import { SectionHeader } from "../components/PanelState";
import { riskCountLine } from "../features/signals/signalWords";
import { RisksAcrossProjects } from "./SignalsPage";

/**
 * Early warnings (`/warnings`): what Jira and Git say, whatever anyone reports,
 * across projects, for the portfolio's readers. For now Signals' risks and
 * drift as they were; lane P groups them by kind.
 */
export function RisksPage() {
  const portfolio = useQuery({
    queryKey: ["portfolio", "risks"],
    queryFn: () => apiClient.portfolioRisks(),
  });
  return (
    <>
      <SectionHeader
        title="Early warnings"
        meta={
          (portfolio.data ? riskCountLine(portfolio.data) : null) ??
          "What Jira and Git say, whatever anyone reports. Findings attach to work items."
        }
      />
      <RisksAcrossProjects portfolio={portfolio} />
    </>
  );
}
