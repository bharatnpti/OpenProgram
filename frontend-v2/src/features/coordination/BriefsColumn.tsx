import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";

import { apiClient } from "../../api/client";
import { useRole } from "../../app/role";
import { Card } from "../../components/ui/Card";
import {
  BRIEF_KIND_LABEL,
  BRIEF_KINDS,
  briefKindParam,
  latestBriefPerScopePerDay,
  withBriefKindParam,
} from "../../lib/briefs";
import { cn } from "../../lib/utils";
import type { BriefKind } from "../../api/schema";

const FILTERS: { kind: BriefKind | undefined; label: string }[] = [
  { kind: undefined, label: "All" },
  ...BRIEF_KINDS.map((kind) => ({ kind, label: BRIEF_KIND_LABEL[kind] })),
];

export function BriefsColumn() {
  const { canReadAggregate } = useRole();
  // The filter lives in the URL so Today's "All briefs" can open on one kind.
  const [searchParams, setSearchParams] = useSearchParams();
  const kind = briefKindParam(searchParams);

  // Briefs summarise a pod, project or programme, so the endpoint requires an
  // aggregate read. Asking anyway would 403 and leave the heading standing over
  // nothing, which reads as "no briefs" rather than "not yours to see".
  //
  // The kind is filtered by the endpoint, not here. The newest 20 of every kind
  // are mostly daily pod briefs, so filtering those would find last week's exec
  // brief missing when it is only further back.
  const briefs = useQuery({
    queryKey: ["persona", "briefs", kind ?? "all"],
    queryFn: () => apiClient.personaBriefs(kind, 20),
    staleTime: 5 * 60_000,
    enabled: canReadAggregate,
  });

  function selectKind(next: BriefKind | undefined) {
    setSearchParams((current) => withBriefKindParam(current, next), { replace: true });
  }

  return (
    <div className="flex flex-col gap-5">
      <h2 className="text-[18px] font-bold">Briefs</h2>
      {canReadAggregate ? (
        <div className="flex flex-wrap gap-2">
          {FILTERS.map((item) => (
            <button
              key={item.label}
              type="button"
              aria-pressed={kind === item.kind}
              onClick={() => selectKind(item.kind)}
              className={cn(
                "flex h-10 items-center rounded-full px-4 text-[14px] font-bold",
                kind === item.kind
                  ? "bg-magenta text-white"
                  : "border border-grey-border bg-white text-ink hover:bg-grey-fill",
              )}
            >
              {item.label}
            </button>
          ))}
        </div>
      ) : (
        <p className="text-sm text-grey-secondary">
          Pod, project and executive briefs need a team or executive role.
        </p>
      )}
      {briefs.isError ? (
        <p className="text-sm text-grey-secondary">
          Briefs could not be loaded:{" "}
          {briefs.error instanceof Error ? briefs.error.message : "unknown error"}
        </p>
      ) : null}
      {latestBriefPerScopePerDay(briefs.data?.briefs ?? []).map((brief) => (
        <Card key={`${brief.kind}-${brief.scope_id}-${brief.generated_at}`} padding="p-6">
          <div className="text-xs font-bold uppercase tracking-wide text-magenta">
            {BRIEF_KIND_LABEL[brief.kind]}
          </div>
          <h3 className="mt-2 text-[17px] font-bold">{brief.title}</h3>
          <div className="mt-1 text-xs text-grey-secondary">
            {new Date(brief.generated_at).toLocaleDateString()}
          </div>
          <p className="mt-2 text-[14px] leading-relaxed text-grey-body">{brief.body}</p>
        </Card>
      ))}
      {briefs.data && briefs.data.briefs.length === 0 ? (
        <p className="text-sm text-grey-secondary">
          {kind === undefined
            ? "No briefs generated yet."
            : `No ${BRIEF_KIND_LABEL[kind].toLowerCase()} briefs generated yet.`}
        </p>
      ) : null}
    </div>
  );
}
