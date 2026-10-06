import { useParams } from "react-router-dom";

import { ForecastSection } from "../features/overall/ForecastSection";
import { ReportsHeader } from "../features/reports/ReportsHeader";
import { GatesSection } from "../features/overall/GatesSection";
import { QuestionsSection } from "../features/overall/QuestionsSection";
import { RequirementsSection } from "../features/overall/RequirementsSection";
import { RisksSection } from "../features/overall/RisksSection";

/**
 * The project's state since it started, read today. Each section is its own
 * query, so one refused or slow read never blanks the others.
 */
export function OverallPage() {
  const { projectId = "" } = useParams();

  return (
    <>
      <ReportsHeader projectId={projectId} />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-12">
        <ForecastSection projectId={projectId} />
        <RequirementsSection projectId={projectId} />
        <GatesSection projectId={projectId} />
        <RisksSection projectId={projectId} />
        <QuestionsSection projectId={projectId} />
      </div>
    </>
  );
}
