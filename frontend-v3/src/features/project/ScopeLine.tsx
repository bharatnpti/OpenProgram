import { Link } from "react-router-dom";

import { useRole } from "../../app/role";
import { useBaselines, useHygiene } from "../overall/queries";
import { VizCard } from "../overall/viz";
import { scopeParts } from "./scopeWords";

/**
 * Scope in one line: the planning baseline and Jira hygiene counted, with Open
 * scope control for the full sections (Mark planning complete, the findings).
 * Each read is the role's own: the baseline for the project's readers, hygiene
 * for the readiness board's; a role with neither gets no line.
 */
export function ScopeLine({ projectId, releaseId }: { projectId: string; releaseId: string }) {
  const { access } = useRole();
  const baselines = useBaselines(projectId, releaseId || undefined);
  const hygiene = useHygiene(projectId, releaseId || undefined);
  if (!baselines.readable && !hygiene.readable) return null;
  const parts = scopeParts(baselines.query.data, hygiene.query.data);
  const loading = baselines.query.isLoading || hygiene.query.isLoading;
  return (
    <section id="scope" aria-labelledby="scope-h" className="scroll-mt-28">
      <VizCard className="gap-2">
        <h2 id="scope-h" className="text-[16px] font-extrabold">
          Scope
        </h2>
        <p className="text-[14px] text-grey-body text-pretty">
          {parts && parts.length > 0
            ? parts.join(" · ")
            : loading
              ? "Loading…"
              : "The planning baseline and Jira hygiene did not load."}
        </p>
        {access.pages.scope ? (
          <Link
            to={`/scope?project=${encodeURIComponent(projectId)}`}
            className="w-fit text-[13px] font-bold max-sm:inline-flex max-sm:min-h-11 max-sm:items-center"
          >
            Open scope control →
          </Link>
        ) : null}
      </VizCard>
    </section>
  );
}
