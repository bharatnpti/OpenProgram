import { useQuery } from "@tanstack/react-query";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { Panel, RagDot } from "../../components/ui/Bits";
import { FactorsPanel } from "../delivery/NodeBits";
import { setBy } from "../delivery/factors";
import { HealthTrend } from "../hub/HealthTrend";
import { healthSentence } from "../portfolio/heat";
import { PrFlowSection } from "../signals/PrFlowSection";

/**
 * A project's health today, on its Early warnings, apart from the date: the
 * one reason that sets its colour, every reason behind it (not for an
 * executive, who reads no one behind them), and its health over the last 30
 * days. Labelled as health, so it never reads as the date verdict.
 */
export function TodaysHealth({ projectId, name }: { projectId: string; name: string }) {
  const { access } = useRole();
  const names = !access.hub.countsOnly;
  const progress = useQuery({
    queryKey: ["project", projectId, "progress"],
    queryFn: () => apiClient.projectProgress(projectId),
  });
  const p = progress.data;
  const sets = p ? setBy(p.rag, p.factors) : null;

  return (
    <section id="health" aria-labelledby="health-h" className="scroll-mt-28">
      <SectionHeader
        id="health-h"
        title="Today's health"
        meta="What sets the project's colour today, apart from the date"
      />
      <div className="grid grid-cols-[minmax(0,1fr)] gap-4 lg:grid-cols-2">
        <Panel title="Today">
          <PanelState
            isLoading={progress.isLoading}
            error={progress.error}
            onRetry={() => void progress.refetch()}
          >
            {p ? (
              <p className="flex items-start gap-2 text-[14px] text-grey-body">
                <RagDot rag={p.rag} className="mt-1.5" />
                <span>
                  <span className="font-bold text-ink">{healthSentence(name, p.rag)}</span>{" "}
                  {/* The header says what sets it ("Today: …"), except to a reader who sees no names. */}
                  {names ? null : sets ? `Set by: ${sets.text}` : "Nothing recorded for it yet."}
                </span>
              </p>
            ) : null}
          </PanelState>
        </Panel>
        <HealthTrend level="project" id={projectId} name={name} />
      </div>
      {names && p && p.factors.length > 1 ? (
        <div className="mt-4">
          <FactorsPanel
            title="Every reason behind today's health"
            factors={p.factors}
            names={p.source_names}
            always
          />
        </div>
      ) : null}
    </section>
  );
}

/**
 * The review flow for one project, in its compact form: how long its merge
 * requests wait in each stage, from Git. The compact form links to the full
 * Review flow itself.
 */
export function ProjectReviewFlow({ projectId }: { projectId: string }) {
  return (
    <section id="flow" aria-labelledby="flow-h" className="scroll-mt-28">
      <SectionHeader
        id="flow-h"
        title="Review flow"
        meta="How long this project's merge requests wait, from Git, whatever anyone reports"
      />
      <PrFlowSection full={false} enabled defaultScope={`project:${projectId}`} />
    </section>
  );
}
