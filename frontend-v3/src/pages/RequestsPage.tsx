import { useRole } from "../app/role";
import { SectionHeader } from "../components/PanelState";
import { Requests } from "./CoordinationPage";

/**
 * Requests (`/requests`): who is waiting on whom, for whoever may act on some
 * ask. For now Coordination's board as it was; lane R builds the table and the
 * trail.
 */
export function RequestsPage() {
  const { coordination } = useRole().access;
  return (
    <>
      <SectionHeader
        title="Requests"
        meta="Who is waiting on whom across the teams. Your own asks are on Today."
      />
      <Requests defaultScope={coordination.boardScope} />
    </>
  );
}
