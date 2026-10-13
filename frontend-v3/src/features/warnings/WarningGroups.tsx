import { useState } from "react";
import { Link } from "react-router-dom";

import type { WarningGroupDto, WarningItemDto } from "../../api/schema";
import { projectPath } from "../../app/links";
import { RagDot } from "../../components/ui/Bits";
import { VizPanel } from "../portfolio/VizPanel";
import { ageWords, emptyWords, itemLine, ownerWords, SHOWN } from "./warningWords";

/**
 * One kind of warning: its count, and its items oldest first, five then all.
 * Each item is the work item's key, how old it is, one line, and who acts on
 * it. Colour is spent only on the severity dot. A group the reader reads only
 * as a count says the count.
 */
export function WarningGroup({
  group,
  seenSince,
}: {
  group: WarningGroupDto;
  seenSince: string | null | undefined;
}) {
  const [all, setAll] = useState(false);
  const items = all ? group.items : group.items.slice(0, SHOWN);
  const headingId = `warnings-${group.kind}`;
  return (
    <VizPanel
      title={group.title}
      titleId={headingId}
      note={<span className="text-[13px] font-extrabold text-ink tabular-nums">{group.count}</span>}
    >
      {group.counts_only ? (
        // The count beside the title is all this reader reads: it is not said twice.
        group.count === 0 ? (
          <p className="text-[14px] text-grey-secondary">{emptyWords(group.kind)}</p>
        ) : null
      ) : group.items.length === 0 ? (
        <p className="text-[14px] text-grey-secondary">{emptyWords(group.kind)}</p>
      ) : (
        <>
          <ul aria-labelledby={headingId}>
            {items.map((item) => (
              <WarningRow key={item.id} item={item} group={group} seenSince={seenSince} />
            ))}
          </ul>
          {group.count > group.items.length ? (
            <p className="mt-2 text-[12px] text-grey-secondary">
              And {group.count - group.items.length} more in other pods.
            </p>
          ) : null}
          {group.items.length > SHOWN ? (
            <button
              type="button"
              onClick={() => setAll((value) => !value)}
              className="mt-2 text-[13px] font-bold text-ink underline"
              aria-expanded={all}
            >
              {all ? "Show fewer" : `Show all ${group.items.length}`}
            </button>
          ) : null}
        </>
      )}
    </VizPanel>
  );
}

function WarningRow({
  item,
  group,
  seenSince,
}: {
  item: WarningItemDto;
  group: WarningGroupDto;
  seenSince: string | null | undefined;
}) {
  return (
    <li className="flex min-w-0 items-start gap-3 border-t border-grey-border py-3 first:border-t-0">
      <RagDot rag={item.severity} className="mt-1.5" />
      <div className="min-w-0 flex-1">
        <p className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-[14px]">
          <span className="font-extrabold text-ink break-words">
            {item.evidence_url ? (
              <a href={item.evidence_url} target="_blank" rel="noreferrer">
                {item.key}
              </a>
            ) : (
              item.key
            )}
          </span>
          <span className="min-w-0 text-grey-body break-words">{itemLine(item)}</span>
        </p>
        <p className="mt-0.5 text-[12px] text-grey-secondary">
          {ownerWords(item, group.kind)} ·{" "}
          <Link to={projectPath(item.project_id, "warnings")} className="text-grey-secondary">
            {item.project_name}
          </Link>
          {item.watermelon ? " · owner says on track" : ""}
        </p>
      </div>
      <span className="flex-none text-right text-[12px] font-bold text-grey-secondary tabular-nums">
        {ageWords(item, seenSince)}
      </span>
    </li>
  );
}
