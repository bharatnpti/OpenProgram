import { useState } from "react";

import type { Rag } from "../../api/schema";
import { Card } from "../../components/ui/Card";
import { toneForRag, toneHex } from "../../lib/status";
import { sourcesLabel, whyTitle, type ReasonsState } from "./rollupReasons";

/** Reasons shown before the list is expanded. */
const REASONS_SHOWN = 5;

/**
 * "Why it's red": a node's rollup reasons, worst first, each naming where it
 * comes from. Shared by every Delivery panel so a reason reads the same on a
 * program as on a pod. Renders nothing for a role that may not read them; the
 * panel says so itself.
 */
export function RollupReasonsCard({
  rag,
  noun,
  state,
  className,
}: {
  rag: Rag;
  noun: string;
  state: ReasonsState;
  className?: string;
}) {
  const [showAll, setShowAll] = useState(false);
  if (state.status === "denied") return null;

  const reasons = state.status === "ready" ? state.reasons : [];
  const visible = showAll ? reasons : reasons.slice(0, REASONS_SHOWN);
  const hidden = reasons.length - REASONS_SHOWN;

  return (
    <Card variant="grey" padding="p-6" className={className}>
      <h3 className="text-[18px] font-bold">{whyTitle(rag)}</h3>
      {state.status === "failed" ? (
        <p className="mt-3 text-sm text-grey-secondary">
          The reasons behind this status could not be loaded.
        </p>
      ) : state.status === "loading" ? (
        <p className="mt-3 text-sm text-grey-secondary">Loading the reasons…</p>
      ) : reasons.length === 0 ? (
        <p className="mt-3 text-sm text-grey-secondary">
          No reasons are recorded for this {noun} yet.
        </p>
      ) : (
        <ul className="mt-3 flex flex-col gap-3">
          {visible.map((reason) => (
            <li key={reason.key} className="flex gap-3">
              <span
                className="mt-1.5 inline-block h-2 w-2 shrink-0 rounded-full"
                style={{ backgroundColor: toneHex[toneForRag(reason.contributes)] }}
              />
              <div className="min-w-0">
                <div className="text-[14px] font-bold">{reason.description}</div>
                <div className="mt-0.5 text-[13px] text-grey-secondary">
                  {sourcesLabel(reason.sources)}
                </div>
              </div>
            </li>
          ))}
        </ul>
      )}
      {hidden > 0 ? (
        <button
          type="button"
          onClick={() => setShowAll((value) => !value)}
          className="mt-4 text-[14px] font-bold text-magenta"
        >
          {showAll ? "Show fewer" : `Show ${hidden} more`}
        </button>
      ) : null}
    </Card>
  );
}
