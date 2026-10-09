import { useState } from "react";

import type { ScopeDeliveryResponse } from "../../api/schema";
import { useMemberId, usePods, useProjects } from "../../app/directory";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { DateCells } from "../../components/ui/DateStrip";
import { Pill } from "../../components/ui/Pill";
import { RagChip } from "../../components/ui/RagChip";
import { formatDate } from "../../lib/format";
import { readState } from "../../lib/readState";
import { DeliveryDateDialog } from "../reports/DeliveryDateDialog";
import { Locked } from "../reports/ReportDialog";
import { useReportAccess } from "../reports/useReportAccess";
import { answerChip, answerEdge, answerMeta, gateAnswer, scopeAnswer } from "./answerWords";
import { releaseName } from "./overallWords";
import { useDelivery, useGateBoard, usePodDeliveries, useRequirements } from "./queries";
import { VizCard } from "./viz";

/** The sentence at the top of the card, with the verdict's chip before it when it adds one. */
function Headline({ scope, text }: { scope: ScopeDeliveryResponse; text: string }) {
  const chip = answerChip(scope);
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
      {chip ? (
        <RagChip tone={chip.tone} dot className="h-[26px] px-3 text-[12.5px]">
          {chip.label}
        </RagChip>
      ) : null}
      <h2 className="min-w-0 flex-[1_1_320px] text-[19px] font-extrabold leading-snug text-balance max-sm:text-[17px]">
        {text}
      </h2>
    </div>
  );
}

/**
 * The project's answer line: will it make its date, and if not, why (the
 * strip's cause, moved up), how much is done, and under it the date strip's
 * three cells with Change date for those who may. It replaces the strip's red
 * cause box, its list of reasons, which restated the cells, and the Completion
 * card. For a release picked above, the release's.
 */
export function ProjectAnswer({ projectId, releaseId }: { projectId: string; releaseId: string }) {
  const access = useReportAccess();
  const delivery = useDelivery(projectId);
  // The same read as the requirements section below (one request): the share by story points.
  const requirements = useRequirements(projectId, releaseId || undefined);
  const [editing, setEditing] = useState<{ scope: ScopeDeliveryResponse; title: string } | null>(
    null,
  );
  const data = delivery.query.data;
  const release = releaseId
    ? data?.releases.find((item) => item.scope_id === releaseId)
    : undefined;
  const scope = release ?? data?.project;
  const subject = release ? releaseName(release.name) : (scope?.name ?? "");
  const points = requirements.query.data
    ? {
        percent: requirements.query.data.percent_complete,
        hasPoints: requirements.query.data.has_points,
      }
    : null;
  const offNow = access.pastDay("setProjectDates");

  return (
    <section aria-label="Will the project make its date">
      <PanelState
        isLoading={delivery.query.isLoading}
        error={delivery.query.error}
        onRetry={() => void delivery.query.refetch()}
      >
        {scope ? (
          <VizCard edge={answerEdge(scope)} className="gap-3">
            <Headline scope={scope} text={scopeAnswer(scope, subject)} />
            <p className="text-[13px] text-grey-secondary">
              {answerMeta(scope, release ? subject : "the whole project", points)}
            </p>
            <DateCells
              scope={scope}
              action={
                access.setProjectDates ? (
                  <Pill
                    size="sm"
                    variant="ghost"
                    onClick={() => setEditing({ scope, title: `${subject}: delivery date` })}
                  >
                    {scope.commitment.target_date ? "Change date" : "Set date"}
                  </Pill>
                ) : offNow ? (
                  <Locked>{offNow}</Locked>
                ) : null
              }
            />
            {scope.jira_release_date && scope.jira_release_date !== scope.target ? (
              <p className="text-[12px] text-grey-secondary">
                The Jira release says {formatDate(scope.jira_release_date)}.
              </p>
            ) : null}
          </VizCard>
        ) : null}
      </PanelState>
      {editing ? (
        <DeliveryDateDialog
          scope={editing.scope}
          title={editing.title}
          onClose={() => setEditing(null)}
        />
      ) : null}
    </section>
  );
}

/**
 * A scrum master's answer line, for each of the project's pods they run (the
 * pod read's `can_set_dates`): its verdict and why. Their Today shows the pod's
 * dates in full; here the pod sits against the others, in "Dates by pod" below.
 */
export function ScrumMasterAnswer({ projectId }: { projectId: string }) {
  const projects = useProjects();
  const pods = usePods();
  const project = projects.data?.find((item) => item.id === projectId);
  const podIds = (pods.data ?? [])
    .filter((pod) => project?.pod_ids.includes(pod.id))
    .map((pod) => pod.id);
  const reads = usePodDeliveries(podIds);
  const mine = reads
    .map((read) => read.data)
    .filter((data) => data?.can_set_dates)
    .flatMap((data) => data?.projects.filter((item) => item.project_id === projectId) ?? [])
    .map((item) => item.pod);
  if (mine.length === 0) return null;

  return (
    <section aria-label="Your pod's date" className="grid grid-cols-[minmax(0,1fr)] gap-3">
      {mine.map((scope) => (
        <VizCard key={scope.scope_id} edge={answerEdge(scope)} className="gap-3">
          <Headline scope={scope} text={scopeAnswer(scope, `${scope.name}, the pod you run,`)} />
          <p className="text-[13px] text-grey-secondary">
            Your Today shows its dates in full. Here it sits against the other pods and the
            project&apos;s date.
          </p>
        </VizCard>
      ))}
    </section>
  );
}

/**
 * A developer's answer line: where the gates stand, which they sign test cases
 * off on, and what waits on them. The gate board is the gates section's own read.
 */
export function GateAnswer({ projectId, releaseId }: { projectId: string; releaseId: string }) {
  const { query } = useGateBoard(projectId, releaseId || undefined);
  const { displayName } = useRole();
  const me = useMemberId();
  const board = query.data;
  const answer = board ? gateAnswer(board, { id: me, name: displayName || null }) : null;

  return (
    <section aria-label="Where the gates stand">
      <PanelState {...readState(query)} onRetry={() => void query.refetch()}>
        {answer ? (
          <VizCard edge={answer.edge} className="gap-2">
            <h2 className="text-[19px] font-extrabold leading-snug text-balance max-sm:text-[17px]">
              {answer.text}
            </h2>
            {answer.meta ? <p className="text-[13px] text-grey-secondary">{answer.meta}</p> : null}
          </VizCard>
        ) : null}
      </PanelState>
    </section>
  );
}
