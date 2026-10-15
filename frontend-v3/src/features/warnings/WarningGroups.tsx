import { useState } from "react";
import { Link } from "react-router-dom";

import type { WarningGroupDto, WarningItemDto } from "../../api/schema";
import { useRole } from "../../app/role";
import { RagDot } from "../../components/ui/Bits";
import { VizPanel } from "../portfolio/VizPanel";
import { WARNING_LINKS, type WarningLinks } from "./warningLinks";
import {
  ageWords,
  emptyWords,
  itemLine,
  ownerSays,
  ownerWords,
  podNames,
  SHOWN,
} from "./warningWords";

/** The level a list of warnings is read at: what each row leaves out as said already. */
export type WarningScope = "all" | "project" | "pod";

/**
 * One kind of warning: its count, and its items oldest first, five then all.
 * Each item is the work item's key, how old it is, one line, who acts on it and
 * the pods it sits in, with what its owner last said folded under it where the
 * reader sees the owner. Colour is spent only on the severity dot. A group the
 * reader reads only as a count says the count. `scope` leaves out what the page
 * already says: the project at a project's level, the pod too at a pod's.
 */
export function WarningGroup({
  group,
  seenSince,
  scope = "all",
  links = WARNING_LINKS,
}: {
  group: WarningGroupDto;
  seenSince: string | null | undefined;
  scope?: WarningScope;
  links?: WarningLinks;
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
              <WarningRow
                key={item.id}
                item={item}
                group={group}
                seenSince={seenSince}
                scope={scope}
                links={links}
              />
            ))}
          </ul>
          {group.count > group.items.length ? (
            <p className="mt-2 text-[12px] text-grey-secondary">
              {scope === "pod"
                ? `And ${group.count - group.items.length} more, counted only.`
                : `And ${group.count - group.items.length} more in other pods.`}
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
  scope,
  links,
}: {
  item: WarningItemDto;
  group: WarningGroupDto;
  seenSince: string | null | undefined;
  scope: WarningScope;
  links: WarningLinks;
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
          {ownerWords(item, group.kind)}
          {scope !== "pod" ? <ItemPods item={item} links={links} /> : null}
          {scope === "all" ? (
            <>
              {" · "}
              <Link to={links.project(item)} className="text-grey-secondary">
                {item.project_name}
              </Link>
            </>
          ) : null}
          {item.watermelon ? " · owner says on track" : ""}
        </p>
        <OwnerSaysFold item={item} />
      </div>
      <span className="flex-none text-right text-[12px] font-bold text-grey-secondary tabular-nums">
        {ageWords(item, seenSince)}
      </span>
    </li>
  );
}

/**
 * What the owner last said, folded under a warning: "What Ben says", their
 * words, and where and when they came from. Drawn only where the read sends
 * the owner's words, which it does only to a reader who sees the owner's name;
 * a reader who sees counts and teams never gets the fold, whatever comes back.
 */
export function OwnerSaysFold({ item }: { item: WarningItemDto }) {
  const { access } = useRole();
  const says = access.hub.countsOnly ? null : ownerSays(item);
  if (!says) return null;
  return (
    <details className="mt-0.5">
      <summary className="cursor-pointer list-none text-[12.5px] font-bold text-grey-body underline underline-offset-2 max-sm:inline-flex max-sm:min-h-11 max-sm:items-center [&::-webkit-details-marker]:hidden">
        {says.title}
      </summary>
      <p className="mt-1.5 rounded-xl bg-grey-fill px-2.5 py-2 text-[12.5px] text-grey-body break-words">
        {says.text}
        <span className="mt-1 block text-[11.5px] text-grey-secondary">{says.meta}</span>
      </p>
    </details>
  );
}

/**
 * The pods an item sits in, after who acts on it: each a link to the pod's own
 * warnings where the page offers one, else its name. The owner's team is the
 * same pod more often than not, and is then not said twice.
 */
export function ItemPods({ item, links }: { item: WarningItemDto; links: WarningLinks }) {
  const pods = item.pods.filter((pod) => pod.name !== item.owner_team);
  if (pods.length === 0) return null;
  const toPod = links.pod;
  if (!toPod) return <>{` · ${podNames({ pods })}`}</>;
  return (
    <>
      {pods.map((pod) => (
        <span key={pod.id}>
          {" · "}
          <Link to={toPod(item, pod.id)} className="text-grey-secondary">
            {pod.name}
          </Link>
        </span>
      ))}
    </>
  );
}
