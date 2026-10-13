import { useRole } from "../app/role";
import { SectionHeader } from "../components/PanelState";
import { Flow } from "./SignalsPage";

/**
 * Review flow (`/flow`, D94): where work waits in review, from Git, as Signals'
 * Flow was, opening on the viewer's own part.
 */
export function FlowPage() {
  const { signals } = useRole().access;
  return (
    <>
      <SectionHeader
        title="Review flow"
        meta="How long work waits in review, from Git, whatever anyone reports."
      />
      <Flow scope={signals.flowScope} />
    </>
  );
}
