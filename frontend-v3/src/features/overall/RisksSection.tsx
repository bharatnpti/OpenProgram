import { Link } from "react-router-dom";

import type { Rag } from "../../api/schema";
import { useNames } from "../../app/directory";
import { useRole } from "../../app/role";
import { useShownDay } from "../../app/viewingDate";
import { PanelState, SectionHeader } from "../../components/PanelState";
import { cn } from "../../lib/utils";
import { useRisks } from "./queries";
import { SEVERITY_WORDS, riskLines } from "./riskWords";
import { VizCard } from "./viz";

const DOT: Record<Rag, string> = {
  red: "bg-(--op-viz-red-solid)",
  amber: "bg-rag-amber",
  green: "bg-rag-green",
  unknown: "bg-rag-unknown-bg",
};

/**
 * Risks and drift from hard signals, one line each, worst and oldest first:
 * the colour is spent on the severity dot only, and what the owner says folds
 * under the line. The escalation matrix that decides who an ask reaches is kept
 * on Admin › Escalation; an admin gets a link to it here.
 */
export function RisksSection({ projectId }: { projectId: string }) {
  const risks = useRisks(projectId);
  const { access } = useRole();
  const names = useNames();
  const today = useShownDay() ?? new Date().toLocaleDateString("en-CA");
  const data = risks.query.data;
  const lines = data ? riskLines(data, names, names.known, today) : [];

  return (
    <section aria-labelledby="rk-h">
      <SectionHeader
        id="rk-h"
        title="Risks and drift"
        meta="From Jira and Git signals, independent of what anyone reports · worst and oldest first"
        actions={
          access.overall.escalationLink ? (
            <Link
              to="/admin?tab=escalation"
              className="text-[13px] font-bold max-sm:inline-flex max-sm:min-h-11 max-sm:items-center"
            >
              Escalation settings
            </Link>
          ) : undefined
        }
      />
      <PanelState
        isLoading={risks.query.isLoading}
        error={risks.query.error}
        onRetry={() => void risks.query.refetch()}
        isEmpty={data ? lines.length === 0 : false}
        emptyText="No open risks or drift for this project."
      >
        <VizCard className="@container">
          <ul className="m-0 grid list-none p-0" aria-label="Open risks and drift">
            {lines.map((line) => (
              <li
                key={line.key}
                className="grid grid-cols-[minmax(0,1fr)] gap-x-3.5 gap-y-1 border-t border-(--op-viz-grid) py-3 first:border-t-0 first:pt-0 last:pb-0 @min-[720px]:grid-cols-[minmax(0,1fr)_180px] @min-[720px]:items-start"
              >
                <div className="min-w-0">
                  <p className="flex min-w-0 items-baseline gap-2 text-[13.5px] font-bold">
                    <i
                      aria-hidden
                      className={cn(
                        "inline-block h-2 w-2 flex-none -translate-y-px rounded-full",
                        DOT[line.severity],
                      )}
                    />
                    <span>
                      <span className="sr-only">{SEVERITY_WORDS[line.severity]}: </span>
                      {line.what}
                    </span>
                  </p>
                  <p className="text-[12.5px] text-grey-secondary">{line.meta}</p>
                  {line.saysTitle && line.says ? (
                    <details className="mt-0.5">
                      <summary className="cursor-pointer list-none text-[12.5px] font-bold text-grey-body underline underline-offset-2 max-sm:inline-flex max-sm:min-h-11 max-sm:items-center [&::-webkit-details-marker]:hidden">
                        {line.saysTitle}
                      </summary>
                      <p className="mt-1.5 rounded-xl bg-grey-fill px-2.5 py-2 text-[12.5px] text-grey-body">
                        {line.says}
                      </p>
                    </details>
                  ) : null}
                </div>
                <p className="text-[12.5px] text-grey-secondary">
                  <span>Who resolves</span>
                  <br />
                  <b className="text-ink">{line.owner || "No one named"}</b>
                </p>
              </li>
            ))}
          </ul>
        </VizCard>
      </PanelState>
    </section>
  );
}
