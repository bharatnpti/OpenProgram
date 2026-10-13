import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";

import { apiClient } from "../../api/client";
import type { DirectoryItemResponse, ScopeDeliveryResponse } from "../../api/schema";
import { podPath, projectPath } from "../../app/links";
import { useRole } from "../../app/role";
import { PanelState } from "../../components/PanelState";
import { DateCells } from "../../components/ui/DateStrip";
import { RagChip } from "../../components/ui/RagChip";
import { ProjectAnswer } from "../overall/AnswerCard";
import { answerChip, answerEdge, answerMeta, scopeAnswer } from "../overall/answerWords";
import { VizCard } from "../overall/viz";
import type { TodaySubject } from "./queries";

/** Today's first block, for every role but the developer, whose check-in leads instead. */
export function TodayAnswer({ subject }: { subject: TodaySubject }) {
  const { access } = useRole();
  if (subject.kind === "project") {
    return (
      <div className="grid grid-cols-[minmax(0,1fr)] gap-2">
        <ProjectAnswer projectId={subject.projectId} releaseId="" />
        <p className="flex flex-wrap gap-x-5 gap-y-1 text-[13px]">
          <Link
            to={projectPath(subject.projectId, "timeline")}
            className="inline-flex min-h-11 items-center font-bold sm:min-h-0"
          >
            See {subject.name}&apos;s timeline and causes →
          </Link>
          {access.pages.portfolio && subject.scope === "portfolio" ? (
            <Link
              to="/portfolio"
              className="inline-flex min-h-11 items-center font-bold sm:min-h-0"
            >
              Will we make the date? Every project →
            </Link>
          ) : null}
        </p>
      </div>
    );
  }
  if (subject.kind === "pod") return <PodAnswer pod={subject.pod} />;
  if (subject.kind === "none" && !subject.loading) {
    return (
      <VizCard>
        <p className="text-[14px] text-grey-body">
          No project or pod is yours yet, so Today has no answer to lead with.
        </p>
      </VizCard>
    );
  }
  return null;
}

/**
 * A scrum master's first block: the worst pod they run, for each project it
 * works on, whether its part will make the date and why, with its dates.
 */
function PodAnswer({ pod }: { pod: DirectoryItemResponse }) {
  const delivery = useQuery({
    queryKey: ["pod-delivery", pod.id],
    queryFn: () => apiClient.podDelivery(pod.id),
  });
  const parts = delivery.data?.projects ?? [];
  return (
    <section aria-label={`Will ${pod.name} make its dates`}>
      <PanelState
        isLoading={delivery.isLoading}
        error={delivery.error}
        onRetry={() => void delivery.refetch()}
        isEmpty={parts.length === 0}
        emptyText={`${pod.name} works on no project with a date yet.`}
      >
        <div className="grid grid-cols-[minmax(0,1fr)] gap-3">
          {parts.map((part) => (
            <PodPart
              key={part.project_id}
              scope={part.pod}
              subject={pod.name}
              project={part.project_name}
            />
          ))}
          <p className="text-[13px]">
            <Link
              to={podPath(pod.id)}
              className="inline-flex min-h-11 items-center font-bold sm:min-h-0"
            >
              Open {pod.name} →
            </Link>
          </p>
        </div>
      </PanelState>
    </section>
  );
}

function PodPart({
  scope,
  subject,
  project,
}: {
  scope: ScopeDeliveryResponse;
  subject: string;
  project: string;
}) {
  const chip = answerChip(scope);
  return (
    <VizCard edge={answerEdge(scope)} className="gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        {chip ? (
          <RagChip tone={chip.tone} dot className="h-[26px] px-3 text-[12.5px]">
            {chip.label}
          </RagChip>
        ) : null}
        <h2 className="min-w-0 flex-[1_1_320px] text-[19px] font-extrabold leading-snug text-balance max-sm:text-[17px]">
          {scopeAnswer(scope, `${subject}, on ${project},`)}
        </h2>
      </div>
      <p className="text-[13px] text-grey-secondary">
        {answerMeta(scope, `${subject}'s part of ${project}`, null)}
      </p>
      <DateCells scope={scope} />
    </VizCard>
  );
}
