import { useRole } from "../app/role";
import { SectionHeader } from "../components/PanelState";
import { ReportsTabs } from "../features/reports/ReportsTabs";
import { Briefs } from "./CoordinationPage";

/** Reports › Briefs (`/reports/briefs`): the narrative briefs, opening on the role's own kind. */
export function BriefsPage() {
  const { coordination } = useRole().access;
  return (
    <>
      <SectionHeader title="Reports" meta="Briefs written from the day's facts, newest first." />
      <ReportsTabs />
      <Briefs fallback={coordination.defaultBrief} />
    </>
  );
}
